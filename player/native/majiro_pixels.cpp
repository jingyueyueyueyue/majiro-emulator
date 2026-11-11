// Windows image I/O and bounded Majiro page operations.
// ABI: char* RuriPixels(const char*), void Delete(const char*).
// Pixel copies preserve all RGBA bytes, including transparent destination data.
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <commdlg.h>
#include <wincodec.h>
#include <wrl/client.h>
#include <algorithm>
#include <array>
#include <cstdint>
#include <cmath>
#include <cstdlib>
#include <cwchar>
#include <cstring>
#include <map>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include "../core/screen_view.hpp"
using Microsoft::WRL::ComPtr;
struct Page {
    int w=0,h=0;
    int depth=32;
    int drawmode=0;
    int antidata=0;
    bool owns_alpha=false;
    bool release_requested=false;
    bool alpha=false;
    std::array<uint32_t,256> palette{};
    std::vector<uint8_t> rgba;
};
static std::map<int,Page> pages;
static std::vector<std::wstring> private_fonts;
static std::vector<uint8_t> last_presented;
static int last_width=0,last_height=0;
static majiro::screen::Frame screen_view;
static unsigned frame_session=0;
static std::mutex guard;
constexpr size_t PAGE_LIMIT=64u*1024*1024, TOTAL_LIMIT=256u*1024*1024;
static void require(bool value,const char* why) {if(!value)throw std::runtime_error(why);}
static void checked(HRESULT hr,const char* why) {
    if(FAILED(hr)) {std::ostringstream s;s<<why<<" HRESULT="<<std::hex<<uint32_t(hr);
        throw std::runtime_error(s.str());}
}
static std::wstring wide(const std::string& input) {
    int length=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,input.data(),int(input.size()),nullptr,0);
    require(length>0,"invalid UTF-8 path");
    std::wstring result(length,L'\0');
    MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,input.data(),int(input.size()),&result[0],length);
    return result;
}
static std::wstring file_path(const std::string& input) {
    auto path=wide(input);
    if(path.rfind(L"\\\\?\\",0)==0)return path;
    DWORD length=GetFullPathNameW(path.c_str(),0,nullptr,nullptr);
    require(length>0,"absolute file path unavailable");
    std::wstring absolute(length,L'\0');
    DWORD got=GetFullPathNameW(path.c_str(),length,absolute.data(),nullptr);
    require(got>0&&got<length,"absolute file path truncated");
    absolute.resize(got);
    if(absolute.rfind(L"\\\\",0)==0)return L"\\\\?\\UNC\\"+absolute.substr(2);
    return L"\\\\?\\"+absolute;
}
static std::string utf8(const std::wstring& input){
    int length=WideCharToMultiByte(CP_UTF8,0,input.data(),int(input.size()),nullptr,0,nullptr,nullptr);
    std::string result(length,'\0');
    WideCharToMultiByte(CP_UTF8,0,input.data(),int(input.size()),result.data(),length,nullptr,nullptr);
    return result;
}
static int font_cell_offset(HDC dc,const TEXTMETRICW& metrics,int em_height){
    // GDI's internal leading includes both sides of the OpenType em.
    // Subtracting it all removes real ascenders. Align the em baseline
    // to the font's typographic ascender instead.
    std::array<uint8_t,2> units_bytes{},ascent_bytes{};
    int baseline=MulDiv(em_height,4,5);
    if(GetFontData(dc,0x64616568,18,units_bytes.data(),2)==2&&
       GetFontData(dc,0x322f534f,68,ascent_bytes.data(),2)==2){
        const int units=int(units_bytes[0])*256+units_bytes[1];
        const int ascent=int16_t(int(ascent_bytes[0])*256+ascent_bytes[1]);
        if(units>0&&ascent>0&&ascent<=2*units)baseline=MulDiv(em_height,ascent,units);
    }
    return metrics.tmAscent-baseline;
}
static size_t used() {size_t bytes=0;for(const auto& p:pages)bytes+=p.second.rgba.size();return bytes;}
static Page allocate(int w,int h,bool alpha) {
    require(w>0 && h>0 && w<=32768 && h<=32768,"invalid page dimensions");
    const size_t bytes=size_t(w)*size_t(h)*4;
    require(bytes<=PAGE_LIMIT && used()+bytes<=TOTAL_LIMIT,"page memory budget exceeded");
    Page p;p.w=w;p.h=h;p.alpha=alpha;p.rgba.resize(bytes,0);
    if(!alpha)for(size_t i=3;i<bytes;i+=4)p.rgba[i]=255;
    return p;
}
static Page& get(int id) {
    auto item=pages.find(id);require(item!=pages.end(),"unknown page handle");return item->second;
}
static void collect_released() {
    bool again=true;
    while(again){
        again=false;
        for(auto it=pages.begin();it!=pages.end();++it){
            if(!it->second.release_requested)continue;
            bool used=false;
            for(const auto& owner:pages)if(owner.second.antidata==it->first){used=true;break;}
            if(used)continue;
            if(it->second.owns_alpha){
                auto alpha=pages.find(it->second.antidata);
                if(alpha!=pages.end())alpha->second.release_requested=true;
            }
            pages.erase(it);again=true;break;
        }
    }
}
static uint8_t alpha_at(const Page& p,size_t pixel) {
    if(!p.antidata)return p.rgba[pixel*4+3];
    return uint8_t(255-get(p.antidata).rgba[pixel*4]);
}
static void put(int id,Page&& value) {
    require(id>0 && pages.find(id)==pages.end(),"duplicate/invalid page handle");
    pages.emplace(id,std::move(value));
}
static ComPtr<IWICImagingFactory> factory() {
    ComPtr<IWICImagingFactory> value;
    checked(CoCreateInstance(CLSID_WICImagingFactory,nullptr,CLSCTX_INPROC_SERVER,
        IID_PPV_ARGS(value.GetAddressOf())),"WIC factory");
    return value;
}
struct ScopeCom {
    HRESULT hr;
    ScopeCom():hr(CoInitializeEx(nullptr,COINIT_MULTITHREADED)) {
        require(SUCCEEDED(hr)||hr==RPC_E_CHANGED_MODE,"COM unavailable");
    }
    ~ScopeCom(){if(SUCCEEDED(hr))CoUninitialize();}
};
static Page load(const std::string& path,bool alpha,bool indexed=false) {
    ScopeCom com;auto f=factory();
    ComPtr<IWICBitmapDecoder> decoder;
    checked(f->CreateDecoderFromFilename(file_path(path).c_str(),nullptr,GENERIC_READ,
        WICDecodeMetadataCacheOnLoad,decoder.GetAddressOf()),"decode PNG");
    ComPtr<IWICBitmapFrameDecode> frame;
    checked(decoder->GetFrame(0,frame.GetAddressOf()),"PNG frame");
    UINT w=0,h=0;checked(frame->GetSize(&w,&h),"PNG dimensions");
    Page page=allocate(int(w),int(h),alpha);
    if(indexed){
        WICPixelFormatGUID format;checked(frame->GetPixelFormat(&format),"index format");
        require(IsEqualGUID(format,GUID_WICPixelFormat8bppIndexed),"indexed PNG required");
        std::vector<uint8_t> indices(size_t(w)*h);
        checked(frame->CopyPixels(nullptr,w,UINT(indices.size()),indices.data()),"indexed pixels");
        ComPtr<IWICPalette> palette;checked(f->CreatePalette(palette.GetAddressOf()),"palette");
        checked(frame->CopyPalette(palette.Get()),"indexed palette");
        UINT actual=0;checked(palette->GetColors(256,page.palette.data(),&actual),"palette colors");
        page.depth=8;
        for(size_t i=0;i<indices.size();++i){
            page.rgba[i*4]=page.rgba[i*4+1]=page.rgba[i*4+2]=indices[i];
            page.rgba[i*4+3]=255;
        }
        return page;
    }
    ComPtr<IWICFormatConverter> converter;
    checked(f->CreateFormatConverter(converter.GetAddressOf()),"pixel converter");
    checked(converter->Initialize(frame.Get(),GUID_WICPixelFormat32bppRGBA,
        WICBitmapDitherTypeNone,nullptr,0,WICBitmapPaletteTypeCustom),"RGBA conversion");
    checked(converter->CopyPixels(nullptr,w*4,UINT(page.rgba.size()),page.rgba.data()),"PNG pixels");
    if(!alpha)for(size_t i=3;i<page.rgba.size();i+=4)page.rgba[i]=255;
    return page;
}
static void png(const Page& p,const std::string& path) {
    ScopeCom com;auto f=factory();ComPtr<IWICStream> stream;
    checked(f->CreateStream(stream.GetAddressOf()),"PNG stream");
    checked(stream->InitializeFromFilename(file_path(path).c_str(),GENERIC_WRITE),"PNG output");
    ComPtr<IWICBitmapEncoder> encoder;
    checked(f->CreateEncoder(GUID_ContainerFormatPng,nullptr,encoder.GetAddressOf()),"PNG encoder");
    checked(encoder->Initialize(stream.Get(),WICBitmapEncoderNoCache),"PNG encoder initialize");
    ComPtr<IWICBitmapFrameEncode> frame;ComPtr<IPropertyBag2> properties;
    checked(encoder->CreateNewFrame(frame.GetAddressOf(),properties.GetAddressOf()),"PNG new frame");
    checked(frame->Initialize(properties.Get()),"PNG frame initialize");
    checked(frame->SetSize(p.w,p.h),"PNG size");
    WICPixelFormatGUID format=GUID_WICPixelFormat32bppBGRA;
    checked(frame->SetPixelFormat(&format),"PNG format");
    require(IsEqualGUID(format,GUID_WICPixelFormat32bppBGRA),"PNG encoder changed pixel format");
    std::vector<uint8_t> bgra=p.rgba;
    for(size_t i=0;i<bgra.size();i+=4){
        if(p.depth==8){
            uint32_t color=p.palette[p.rgba[i]];
            bgra[i]=uint8_t(color);bgra[i+1]=uint8_t(color>>8);
            bgra[i+2]=uint8_t(color>>16);bgra[i+3]=255;
        }else {std::swap(bgra[i],bgra[i+2]);bgra[i+3]=alpha_at(p,i/4);}
    }
    checked(frame->WritePixels(p.h,p.w*4,UINT(bgra.size()),bgra.data()),"PNG write");
    checked(frame->Commit(),"PNG frame commit");checked(encoder->Commit(),"PNG commit");
}
static uint32_t png_crc(const uint8_t* data,size_t length){
    static const auto table=[]{
        std::array<std::array<uint32_t,256>,8> t{};
        for(uint32_t i=0;i<256;++i){
            uint32_t c=i;for(int bit=0;bit<8;++bit)c=(c>>1)^((c&1)?0xedb88320u:0);
            t[0][i]=c;
        }
        for(int n=1;n<8;++n)for(int i=0;i<256;++i)
            t[n][i]=(t[n-1][i]>>8)^t[0][t[n-1][i]&255];
        return t;
    }();
    uint32_t crc=0xffffffffu;
    while(length>=8){
        uint32_t a,b;std::memcpy(&a,data,4);std::memcpy(&b,data+4,4);a^=crc;
        crc=table[7][a&255]^table[6][(a>>8)&255]^table[5][(a>>16)&255]^table[4][a>>24]^
            table[3][b&255]^table[2][(b>>8)&255]^table[1][(b>>16)&255]^table[0][b>>24];
        data+=8;length-=8;
    }
    while(length--)crc=(crc>>8)^table[0][(crc^*data++)&255];
    return ~crc;
}
static void png_fast(const Page& p,const std::string& path){
    // Completed screens are opaque: RGB avoids transmitting redundant alpha.
    // PNG with stored DEFLATE blocks: identical visible pixels, no expensive
    // per-frame compression. Only the last two transient images are retained.
    const size_t row=size_t(p.w)*3,raw_size=(row+1)*p.h;
    std::vector<uint8_t> raw(raw_size);
    for(int y=0;y<p.h;++y){
        auto* target=raw.data()+size_t(y)*(row+1)+1;
        const auto* source=p.rgba.data()+size_t(y)*p.w*4;
        for(int x=0;x<p.w;++x){
            target[x*3]=source[x*4];target[x*3+1]=source[x*4+1];target[x*3+2]=source[x*4+2];
        }
    }
    uint32_t a=1,b=0;
    for(size_t offset=0;offset<raw.size();){
        const size_t end=std::min(raw.size(),offset+5552);
        for(;offset<end;++offset){a+=raw[offset];b+=a;}
        a%=65521;b%=65521;
    }
    std::vector<uint8_t> z;z.reserve(raw.size()+raw.size()/65535*5+16);
    z.push_back(0x78);z.push_back(0x01);
    for(size_t offset=0;offset<raw.size();){
        const uint16_t length=uint16_t(std::min(size_t(65535),raw.size()-offset));
        z.push_back(offset+length==raw.size()?1:0);
        z.push_back(uint8_t(length));z.push_back(uint8_t(length>>8));
        z.push_back(uint8_t(~length));z.push_back(uint8_t((~length)>>8));
        z.insert(z.end(),raw.begin()+offset,raw.begin()+offset+length);offset+=length;
    }
    auto be=[](std::vector<uint8_t>& out,uint32_t n){
        out.push_back(uint8_t(n>>24));out.push_back(uint8_t(n>>16));
        out.push_back(uint8_t(n>>8));out.push_back(uint8_t(n));
    };
    be(z,(b<<16)|a);
    std::vector<uint8_t> output{137,80,78,71,13,10,26,10};
    output.reserve(z.size()+80);
    auto chunk=[&](const char* type,const std::vector<uint8_t>& bytes){
        be(output,uint32_t(bytes.size()));size_t start=output.size();
        output.insert(output.end(),type,type+4);output.insert(output.end(),bytes.begin(),bytes.end());
        be(output,png_crc(output.data()+start,bytes.size()+4));
    };
    std::vector<uint8_t> header;be(header,uint32_t(p.w));be(header,uint32_t(p.h));
    header.insert(header.end(),{8,2,0,0,0});
    chunk("IHDR",header);chunk("IDAT",z);chunk("IEND",{});
    HANDLE file=CreateFileW(file_path(path).c_str(),GENERIC_WRITE,FILE_SHARE_READ,nullptr,
        CREATE_ALWAYS,FILE_ATTRIBUTE_TEMPORARY|FILE_FLAG_SEQUENTIAL_SCAN,nullptr);
    require(file!=INVALID_HANDLE_VALUE,"live PNG output unavailable");
    DWORD written=0;BOOL success=WriteFile(file,output.data(),DWORD(output.size()),&written,nullptr);
    CloseHandle(file);
    require(success&&written==output.size(),"live PNG output truncated");
}
static void composite(Page& destination,const Page& source,int sx,int sy,int w,int h,
    double x,double y,double xs,double ys,double angle,int anti,bool centered,bool raw=false,
    int clipx=0,int clipy=0,int clipw=32768,int cliph=32768,bool allow_fast=true){
    if(xs==0||ys==0||w<=0||h<=0||anti>=255)return;
    const int mode=raw?0:source.drawmode;
    const bool special=mode==1||mode==3||mode==4;
    auto blend_mode=[&](uint8_t* out,const uint8_t* color){
        if(!special)return false;
        for(int c=0;c<(mode==3?3:4);++c){
            const int d=out[c],s=color[c];
            if(mode==1)out[c]=uint8_t(std::min(255,d+(anti==0?s:(s*(255-anti)>>8))));
            else if(mode==3)out[c]=uint8_t((((s+1)*d>>8)*(255-anti)+d*(anti+1))>>8);
            else out[c]=uint8_t(anti==0?std::max(d,s):
                (std::max(d,s)*(256-anti)+d*anti)>>8);
        }
        return true;
    };
    const uint8_t* mask_pixels=source.antidata?get(source.antidata).rgba.data():nullptr;
    const bool has_mask=source.alpha||mask_pixels;
    const double base_x=x-(centered?w/2.0:0),base_y=y-(centered?h/2.0:0);
    if(allow_fast&&source.depth==32&&xs==1&&ys==1&&std::fmod(angle,360.0)==0&&
       base_x==std::floor(base_x)&&base_y==std::floor(base_y)){
        if(base_x>=destination.w||base_y>=destination.h||base_x+w<=0||base_y+h<=0)return;
        const int dx0=int(base_x),dy0=int(base_y);
        const int left=std::max({0,-sx,-dx0,clipx-dx0});
        const int top=std::max({0,-sy,-dy0,clipy-dy0});
        const int right=std::min({w,source.w-sx,destination.w-dx0,clipx+clipw-dx0});
        const int bottom=std::min({h,source.h-sy,destination.h-dy0,clipy+cliph-dy0});
        if(right<=left||bottom<=top)return;
        for(int yy=top;yy<bottom;++yy){
            size_t from=(size_t(sy+yy)*source.w+sx+left)*4;
            size_t to=(size_t(dy0+yy)*destination.w+dx0+left)*4;
            const size_t bytes=size_t(std::max(0,right-left))*4;
            if(!special&&!has_mask&&anti==0&&!destination.antidata){
                std::memcpy(destination.rgba.data()+to,source.rgba.data()+from,bytes);continue;
            }
            for(int xx=left;xx<right;++xx,from+=4,to+=4){
                const uint8_t alpha=mask_pixels?uint8_t(255-mask_pixels[from]):source.rgba[from+3];
                auto* out=destination.rgba.data()+to;
                const auto* color=source.rgba.data()+from;
                if(blend_mode(out,color))continue;
                if(raw){
                    std::memcpy(out,color,4);out[3]=alpha;
                    if(destination.antidata){
                        auto& mask=get(destination.antidata);
                        mask.rgba[to]=mask.rgba[to+1]=mask.rgba[to+2]=uint8_t(255-alpha);
                    }
                    continue;
                }
                const int transparency=(has_mask?255-alpha:0)+anti;
                if(transparency>=255||(has_mask&&anti>0&&transparency>=254))continue;
                if(transparency==0){std::memcpy(out,color,4);out[3]=alpha;continue;}
                for(int c=0;c<3;++c)out[c]=uint8_t(
                    (out[c]*(transparency+1)+color[c]*(256-transparency))>>8);
                out[3]=uint8_t((out[3]*(transparency+1)+alpha*(256-transparency))>>8);
            }
        }
        return;
    }
    const double radians=angle*3.14159265358979323846/180.0;
    const double cs=std::cos(radians),sn=std::sin(radians);
    const double origin_x=centered?w/2.0:0,origin_y=centered?h/2.0:0;
    double min_x=1e30,max_x=-1e30,min_y=1e30,max_y=-1e30;
    for(int yy:{0,h})for(int xx:{0,w}){
        const double u=(xx-origin_x)*xs,v=(yy-origin_y)*ys;
        const double dx=x+cs*u-sn*v,dy=y+sn*u+cs*v;
        min_x=std::min(min_x,dx);max_x=std::max(max_x,dx);
        min_y=std::min(min_y,dy);max_y=std::max(max_y,dy);
    }
    if(max_x<=0||max_y<=0||min_x>=destination.w||min_y>=destination.h)return;
    int left=int(std::max(0.0,std::floor(min_x))),right=int(std::min(double(destination.w),std::ceil(max_x)));
    int top=int(std::max(0.0,std::floor(min_y))),bottom=int(std::min(double(destination.h),std::ceil(max_y)));
    left=std::max(left,clipx);right=std::min(right,clipx+clipw);
    top=std::max(top,clipy);bottom=std::min(bottom,clipy+cliph);
    for(int dy=top;dy<bottom;++dy)for(int dx=left;dx<right;++dx){
        const double rx=dx+0.5-x,ry=dy+0.5-y;
        const double u=(cs*rx+sn*ry)/xs+origin_x,v=(-sn*rx+cs*ry)/ys+origin_y;
        if(u<0||v<0||u>=w||v>=h)continue;
        int px=sx+int(std::floor(u)),py=sy+int(std::floor(v));
        if(px<0||py<0||px>=source.w||py>=source.h)continue;
        const size_t from=(size_t(py)*source.w+px)*4,to=(size_t(dy)*destination.w+dx)*4;
        uint8_t color[4];
        if(source.depth==8){
            uint32_t p=source.palette[source.rgba[from]];
            color[0]=uint8_t(p>>16);color[1]=uint8_t(p>>8);color[2]=uint8_t(p);color[3]=255;
        }else for(int c=0;c<4;++c)color[c]=source.rgba[from+c];
        if(blend_mode(destination.rgba.data()+to,color))continue;
        if(raw){
            color[3]=mask_pixels?uint8_t(255-mask_pixels[from]):source.rgba[from+3];
            std::memcpy(destination.rgba.data()+to,color,4);
            if(destination.antidata){
                auto& mask=get(destination.antidata);
                mask.rgba[to]=mask.rgba[to+1]=mask.rgba[to+2]=uint8_t(255-color[3]);
            }
            continue;
        }
        const int mask=has_mask?(mask_pixels?mask_pixels[from]:255-source.rgba[from+3]):0;
        const int transparency=mask+anti;
        if(transparency>=255||(has_mask&&anti>0&&transparency>=254))continue;
        if(transparency==0){std::memcpy(destination.rgba.data()+to,color,4);continue;}
        for(int c=0;c<4;++c)destination.rgba[to+c]=uint8_t(
            (destination.rgba[to+c]*(transparency+1)+color[c]*(256-transparency))>>8);
    }
}
static std::string path_arg(std::istringstream& in) {
    std::string path;std::getline(in>>std::ws,path);
    require(!path.empty() && path.find('\n')==std::string::npos,"missing/invalid path");return path;
}
static std::wstring save_path(const std::string& name){
    require(!name.empty()&&name.size()<=120&&name.front()!='.'&&
        name.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")==std::string::npos,
        "invalid port-save filename");
    CreateDirectoryW(file_path("savedata-dev").c_str(),nullptr);
    CreateDirectoryW(file_path("savedata-dev/port-data").c_str(),nullptr);
    return file_path("savedata-dev/port-data/"+name);
}
static std::string run(const std::string& request) {
    require(request.size()<=65536,"request too long");
    std::istringstream in(request);std::string op;in>>op;
    if(op=="font_dialog"){
        auto split=request.find('\n');require(split!=std::string::npos,"font family missing");
        auto face=wide(request.substr(split+1));
        if(face==L"ＭＳ ゴシック"||face==L"MS Gothic")face=L"Source Han Sans SC";
        LOGFONTW font{};font.lfHeight=-20;font.lfWeight=FW_NORMAL;font.lfCharSet=DEFAULT_CHARSET;
        wcsncpy_s(font.lfFaceName,face.c_str(),_TRUNCATE);
        HWND owner=nullptr;
        EnumWindows([](HWND window,LPARAM value)->BOOL{
            DWORD pid=0;GetWindowThreadProcessId(window,&pid);
            if(pid==GetCurrentProcessId()&&IsWindowVisible(window)&&!GetWindow(window,GW_OWNER)){
                *reinterpret_cast<HWND*>(value)=window;return FALSE;
            }return TRUE;
        },reinterpret_cast<LPARAM>(&owner));
        CHOOSEFONTW choice{};choice.lStructSize=sizeof(choice);choice.hwndOwner=owner;
        choice.lpLogFont=&font;choice.Flags=CF_SCREENFONTS|CF_INITTOLOGFONTSTRUCT|
            CF_FORCEFONTEXIST|CF_NOVERTFONTS|CF_NOSIZESEL|CF_NOSTYLESEL;
        if(ChooseFontW(&choice))return "OK selected\n"+utf8(font.lfFaceName);
        require(CommDlgExtendedError()==0,"font chooser failed");
        return "OK cancelled";
    }
    if(op=="save_exists"||op=="save_read"||op=="save_delete"||op=="save_write"){
        std::string name;require(bool(in>>name),"save filename missing");
        auto path=save_path(name);
        if(op=="save_exists")return GetFileAttributesW(path.c_str())==INVALID_FILE_ATTRIBUTES?"OK 0":"OK 1";
        if(op=="save_delete"){
            BOOL success=DeleteFileW(path.c_str());
            require(success||GetLastError()==ERROR_FILE_NOT_FOUND,"save cleanup failed");
            return "OK";
        }
        if(op=="save_read"){
            HANDLE file=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_DELETE,
                nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
            if(file==INVALID_HANDLE_VALUE){
                require(GetLastError()==ERROR_FILE_NOT_FOUND,"save file unavailable");
                return "OK absent";
            }
            LARGE_INTEGER size{};BOOL valid=GetFileSizeEx(file,&size);
            if(!valid||size.QuadPart<0||size.QuadPart>64000){CloseHandle(file);throw std::runtime_error("save chunk size");}
            std::string data(size_t(size.QuadPart),'\0');DWORD got=0;
            BOOL success=ReadFile(file,data.data(),DWORD(data.size()),&got,nullptr);CloseHandle(file);
            require(success&&got==data.size(),"save chunk truncated");
            require(data.find('\0')==std::string::npos,"binary data in ASCII save chunk");
            return "OK data\n"+data;
        }
        size_t split=request.find('\n');require(split!=std::string::npos,"save data missing");
        auto data=request.substr(split+1);auto temporary=path+L".writing";
        HANDLE file=CreateFileW(temporary.c_str(),GENERIC_WRITE,0,nullptr,CREATE_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,nullptr);
        if(file==INVALID_HANDLE_VALUE)throw std::runtime_error(
            "temporary save file unavailable win32="+std::to_string(GetLastError()));
        DWORD written=0;BOOL success=WriteFile(file,data.data(),DWORD(data.size()),&written,nullptr);
        BOOL flushed=FlushFileBuffers(file);CloseHandle(file);
        if(!success||written!=data.size()||!flushed){DeleteFileW(temporary.c_str());throw std::runtime_error("save write failed");}
        if(!MoveFileExW(temporary.c_str(),path.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)){
            DeleteFileW(temporary.c_str());throw std::runtime_error("save publish failed");
        }
        return "OK";
    }
    if(op=="sha256"){
        auto split=request.find('\n');require(split!=std::string::npos,"hash data missing");
        const auto data=request.substr(split+1);
        BCRYPT_ALG_HANDLE algorithm=nullptr;BCRYPT_HASH_HANDLE hash=nullptr;
        require(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)>=0,
            "SHA256 provider unavailable");
        DWORD object_size=0,returned=0;
        auto status=BCryptGetProperty(algorithm,BCRYPT_OBJECT_LENGTH,
            reinterpret_cast<PUCHAR>(&object_size),sizeof(object_size),&returned,0);
        std::vector<uint8_t> object(object_size);std::array<uint8_t,32> digest{};
        if(status>=0)status=BCryptCreateHash(algorithm,&hash,object.data(),object_size,nullptr,0,0);
        if(status>=0)status=BCryptHashData(hash,
            reinterpret_cast<PUCHAR>(const_cast<char*>(data.data())),ULONG(data.size()),0);
        if(status>=0)status=BCryptFinishHash(hash,digest.data(),ULONG(digest.size()),0);
        if(hash)BCryptDestroyHash(hash);BCryptCloseAlgorithmProvider(algorithm,0);
        require(status>=0,"SHA256 operation failed");
        static const char digits[]="0123456789abcdef";
        std::string result="OK ";
        for(auto value:digest){result+=digits[value>>4];result+=digits[value&15];}
        return result;
    }
    if(op=="batch"){
        size_t offset=request.find('\n');require(offset!=std::string::npos,"batch missing");
        ++offset;
        while(offset<request.size()){
            size_t end=request.find('\n',offset);require(end!=std::string::npos,"batch length missing");
            const size_t length=std::stoul(request.substr(offset,end-offset));
            offset=end+1;require(length>0&&length<=request.size()-offset,"batch command truncated");
            auto command=request.substr(offset,length);
            require(command.find("batch")!=0,"nested native batch");
            auto result=run(command);require(result.rfind("OK",0)==0,"batch command failed");
            offset+=length;
        }
        return "OK";
    }
    if(op=="reset"||op=="reset_pages"){
        pages.clear();last_presented.clear();last_width=last_height=0;
        if(op=="reset")screen_view.reset();
        return "OK";
    }
    if(op=="drawmode"){
        int id,mode;require(bool(in>>id>>mode),"drawmode arguments");
        auto& page=get(id);require(page.depth==32,"drawmode requires 32-bit page");
        require(mode>=0&&mode<=4,"unsupported drawmode");
        page.drawmode=mode;return "OK";
    }
    if(op=="session"){
        // Only our reserved, flat frame-cache filenames are considered.
        // Other running game processes retain their own cache files.
        WIN32_FIND_DATAW item{};
        HANDLE search=FindFirstFileW(L"savedata-dev\\ruri_live_*.*",&item);
        if(search!=INVALID_HANDLE_VALUE){
            do{
                if(item.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY)continue;
                const std::wstring name=item.cFileName;
                if(name.find(L"ruri_live_")!=0)continue;
                if(name.size()<4||(name.substr(name.size()-4)!=L".png"&&
                   name.substr(name.size()-4)!=L".bmp"))continue;
                const size_t end=name.find(L'_',10);
                if(end==std::wstring::npos)continue; // legacy non-PID cache
                const auto digits=name.substr(10,end-10);
                if(digits.empty()||digits.find_first_not_of(L"0123456789")!=std::wstring::npos)continue;
                const DWORD pid=DWORD(std::wcstoul(digits.c_str(),nullptr,10));
                bool expired=pid==GetCurrentProcessId();
                if(!expired){
                    HANDLE process=OpenProcess(SYNCHRONIZE,FALSE,pid);
                    if(process){expired=WaitForSingleObject(process,0)==WAIT_OBJECT_0;CloseHandle(process);}
                    else expired=GetLastError()==ERROR_INVALID_PARAMETER;
                }
                if(expired)DeleteFileW((L"savedata-dev\\"+name).c_str());
            }while(FindNextFileW(search,&item));
            FindClose(search);
        }
        return "OK "+std::to_string(GetCurrentProcessId())+"_"+std::to_string(++frame_session);
    }
    if(op=="fontfile"){
        auto path=wide(path_arg(in));
        if(std::find(private_fonts.begin(),private_fonts.end(),path)==private_fonts.end()){
            require(AddFontResourceExW(path.c_str(),FR_PRIVATE,nullptr)>0,"private UI font unavailable");
            private_fonts.push_back(path);
        }
        return "OK";
    }
    if(op=="create"){int id,w,h,a;require(bool(in>>id>>w>>h>>a),"create arguments");
        int depth=32;in>>depth;require(depth==8||depth==32,"invalid pixel depth");
        auto p=allocate(w,h,a!=0);p.depth=depth;put(id,std::move(p));return "OK";}
    if(op=="load"||op=="load8"){int id,a;require(bool(in>>id>>a),"load arguments");
        put(id,load(path_arg(in),a!=0,op=="load8"));return "OK";}
    if(op=="release"){int id;require(bool(in>>id),"release arguments");
        get(id).release_requested=true;collect_released();return "OK";}
    if(op=="alpha"){int id,mask,extract;require(bool(in>>id>>mask>>extract),"alpha arguments");
        Page& p=get(id);require(p.depth==32 && !p.antidata,"alpha owner invalid");
        Page m=allocate(p.w,p.h,false);m.depth=8;
        for(size_t i=0;i<m.rgba.size();i+=4){
            auto value=uint8_t(extract?255-p.rgba[i+3]:0);
            m.rgba[i]=m.rgba[i+1]=m.rgba[i+2]=value;m.rgba[i+3]=255;
        }
        put(mask,std::move(m));p.antidata=mask;p.owns_alpha=true;return "OK";}
    if(op=="antidata"){int id,mask;require(bool(in>>id>>mask),"antidata arguments");
        Page& p=get(id);require(p.depth==32,"alpha owner must be 32-bit");
        if(mask){Page& m=get(mask);require(m.depth==8,"alpha page must be indexed");
            require(p.w==m.w && p.h==m.h,"alpha dimensions mismatch");}
        if(p.owns_alpha)get(p.antidata).release_requested=true;
        p.antidata=mask;p.owns_alpha=false;collect_released();return "OK";}
    if(op=="swap"){
        int source,sx,sy,w,h,dest,dx,dy;
        require(bool(in>>source>>sx>>sy>>w>>h>>dest>>dx>>dy),"swap arguments");
        if(w<=0||h<=0)return "OK";
        Page& s=get(source);Page& d=get(dest);
        if(s.depth!=d.depth)return "OK";
        const int64_t left=std::max({int64_t(0),-int64_t(sx),-int64_t(dx)});
        const int64_t top=std::max({int64_t(0),-int64_t(sy),-int64_t(dy)});
        const int64_t right=std::min({int64_t(w),int64_t(s.w)-sx,int64_t(d.w)-dx});
        const int64_t bottom=std::min({int64_t(h),int64_t(s.h)-sy,int64_t(d.h)-dy});
        if(right<=left||bottom<=top)return "OK";
        const size_t bytes=size_t(right-left)*4;
        std::vector<uint8_t> row(bytes);
        auto exchange=[&](Page& a,Page& b){
            // Original 0x47DC30 walks upward when source y < target y;
            // same-row overlap uses one saved target row and memmove.
            for(int64_t i=0;i<bottom-top;++i){
                const int64_t y=sy<dy?bottom-1-i:top+i;
                auto* from=a.rgba.data()+(size_t(int64_t(sy)+y)*a.w+size_t(int64_t(sx)+left))*4;
                auto* to=b.rgba.data()+(size_t(int64_t(dy)+y)*b.w+size_t(int64_t(dx)+left))*4;
                std::memcpy(row.data(),to,bytes);
                std::memmove(to,from,bytes);
                std::memcpy(from,row.data(),bytes);
            }
        };
        exchange(s,d);
        // The original swaps masks only when both pages own one.
        if(s.antidata&&d.antidata)exchange(get(s.antidata),get(d.antidata));
        return "OK";
    }
    if(op=="reverse"){
        int id,x,y,w,h;require(bool(in>>id>>x>>y>>w>>h),"reverse arguments");
        auto& p=get(id);
        if(p.depth!=32||w<=0||h<=0)return "OK";
        for(int64_t yy=std::max(y,0);yy<std::min(int64_t(p.h),int64_t(y)+h);++yy)
            for(int64_t xx=std::max(x,0);xx<std::min(int64_t(p.w),int64_t(x)+w);++xx){
                const auto at=(size_t(yy)*p.w+xx)*4;
                for(int c=0;c<3;++c)p.rgba[at+c]=uint8_t(255-p.rgba[at+c]);
            }
        return "OK";
    }
    if(op=="overlayfill"){
        int id,x,y,w,h;uint32_t color;
        require(bool(in>>id>>x>>y>>w>>h>>color),"overlay fill arguments");
        auto& p=get(id);if(p.depth!=32||w<=0||h<=0)return "OK";
        for(int64_t yy=std::max(y,0);yy<std::min(int64_t(p.h),int64_t(y)+h);++yy)
            for(int64_t xx=std::max(x,0);xx<std::min(int64_t(p.w),int64_t(x)+w);++xx){
                const size_t at=(size_t(yy)*p.w+xx)*4;
                for(unsigned c=0;c<3;++c){
                    const unsigned a=p.rgba[at+c],b=(color>>(c*8))&255;
                    p.rgba[at+c]=uint8_t(a<128?(2*a*b/255):(2*(a+b-a*b/255)+1));
                }
            }
        return "OK";
    }
    if(op=="mosaic"){
        int source,sx,sy,w,h,dest,dx,dy,block;uint32_t seed;
        require(bool(in>>source>>sx>>sy>>w>>h>>dest>>dx>>dy>>block>>seed),"mosaic arguments");
        auto& s=get(source);auto& d=get(dest);
        require(block>0,"mosaic block size must be positive");
        if(s.depth!=32||d.depth!=32)return "OK "+std::to_string(seed);
        w=std::max(1,w);h=std::max(1,h);
        const int64_t left=std::max({int64_t(0),-int64_t(sx),-int64_t(dx)});
        const int64_t top=std::max({int64_t(0),-int64_t(sy),-int64_t(dy)});
        const int64_t right=std::min({int64_t(w),int64_t(s.w)-sx,int64_t(d.w)-dx});
        const int64_t bottom=std::min({int64_t(h),int64_t(s.h)-sy,int64_t(d.h)-dy});
        if(right<=left||bottom<=top)return "OK "+std::to_string(seed);
        sx+=int(left);sy+=int(top);dx+=int(left);dy+=int(top);
        w=int(right-left);h=int(bottom-top);
        auto random=[&](){seed=seed*214013u+2531011u;return(seed>>16)&32767u;};
        for(int64_t bx=int64_t(dx)+(w%block)/2-block;bx<int64_t(dx)+w;bx+=block)
            for(int64_t by=int64_t(dy)+(h%block)/2-block;by<int64_t(dy)+h;by+=block){
                const int64_t x0=std::max<int64_t>(dx,bx),y0=std::max<int64_t>(dy,by);
                const int64_t x1=std::min<int64_t>(int64_t(dx)+w,bx+block);
                const int64_t y1=std::min<int64_t>(int64_t(dy)+h,by+block);
                if(x1<=x0||y1<=y0)continue;
                unsigned average[3]={0,0,0};
                for(int sample=0;sample<3;++sample){
                    const auto yy=sy+(y0-dy)+random()%unsigned(y1-y0);
                    const auto xx=sx+(x0-dx)+random()%unsigned(x1-x0);
                    const size_t at=(size_t(yy)*s.w+size_t(xx))*4;
                    for(int c=0;c<3;++c)average[c]+=s.rgba[at+c];
                }
                for(int64_t yy=y0;yy<y1;++yy)for(int64_t xx=x0;xx<x1;++xx){
                    const size_t at=(size_t(yy)*d.w+size_t(xx))*4;
                    for(int c=0;c<3;++c)d.rgba[at+c]=uint8_t(average[c]/3);
                }
            }
        return "OK "+std::to_string(seed);
    }
    if(op=="revmulcopy"||op=="mulcopy"){
        int source,sx,sy,w,h,dest,dx,dy;
        require(bool(in>>source>>sx>>sy>>w>>h>>dest>>dx>>dy),"revmulcopy arguments");
        auto& s=get(source);auto& d=get(dest);
        if(s.depth!=8||d.depth!=8||w<=0||h<=0)return "OK";
        const int64_t left=std::max({int64_t(0),-int64_t(sx),-int64_t(dx)});
        const int64_t top=std::max({int64_t(0),-int64_t(sy),-int64_t(dy)});
        const int64_t right=std::min({int64_t(w),int64_t(s.w)-sx,int64_t(d.w)-dx});
        const int64_t bottom=std::min({int64_t(h),int64_t(s.h)-sy,int64_t(d.h)-dy});
        // Original 0x47D090 walks live source/destination bytes in row order.
        for(int64_t y=top;y<bottom;++y)for(int64_t x=left;x<right;++x){
            const size_t from=(size_t(sy+y)*s.w+sx+x)*4,to=(size_t(dy+y)*d.w+dx+x)*4;
            const uint8_t value=op=="mulcopy"?uint8_t((unsigned(s.rgba[from])+1)*d.rgba[to]>>8):
                uint8_t(255-((255-d.rgba[to])*(256-s.rgba[from])>>8));
            d.rgba[to]=d.rgba[to+1]=d.rgba[to+2]=value;
        }
        return "OK";
    }
    if(op=="copy" || op=="extcopy"){
        int source,sx,sy,w,h,dest,dx,dy;
        require(bool(in>>source>>sx>>sy>>w>>h>>dest>>dx>>dy),"copy arguments");
        int anti=0;if(op=="extcopy")in>>anti;
        require(anti>=0 && anti<=255,"transparency out of range");
        if(w<=0||h<=0)return "OK";
        Page& s=get(source);Page& d=get(dest);
        const int64_t left=std::max({int64_t(0),-int64_t(sx),-int64_t(dx)});
        const int64_t top=std::max({int64_t(0),-int64_t(sy),-int64_t(dy)});
        const int64_t right=std::min({int64_t(w),int64_t(s.w)-sx,int64_t(d.w)-dx});
        const int64_t bottom=std::min({int64_t(h),int64_t(s.h)-sy,int64_t(d.h)-dy});
        if(right>left && bottom>top){
            const size_t row_bytes=size_t(right-left)*4;
            const bool copying=op=="copy";
            const bool masked=s.alpha||s.antidata;
            if(source!=dest&&copying&&!masked){
                for(int64_t y=top;y<bottom;++y)
                    std::memcpy(d.rgba.data()+(size_t(int64_t(dy)+y)*d.w+size_t(int64_t(dx)+left))*4,
                        s.rgba.data()+(size_t(int64_t(sy)+y)*s.w+size_t(int64_t(sx)+left))*4,row_bytes);
                return "OK";
            }
            std::vector<uint8_t> frozen(row_bytes*size_t(bottom-top));
            std::vector<uint8_t> frozen_mask(size_t(right-left)*size_t(bottom-top));
            for(int64_t y=top;y<bottom;++y) {
                std::memcpy(frozen.data()+size_t(y-top)*row_bytes,
                    s.rgba.data()+(size_t(int64_t(sy)+y)*s.w+size_t(int64_t(sx)+left))*4,row_bytes);
                if(masked)for(int64_t x=left;x<right;++x){
                    const auto alpha=alpha_at(s,size_t(int64_t(sy)+y)*s.w+size_t(int64_t(sx)+x));
                    frozen_mask[size_t(y-top)*size_t(right-left)+size_t(x-left)]=uint8_t(255-alpha);
                    if(copying)frozen[size_t(y-top)*row_bytes+size_t(x-left)*4+3]=alpha;
                }
            }
            for(int64_t y=top;y<bottom;++y) {
                auto* destination=d.rgba.data()+(size_t(int64_t(dy)+y)*d.w+size_t(int64_t(dx)+left))*4;
                const auto* from=frozen.data()+size_t(y-top)*row_bytes;
                if(copying)std::memcpy(destination,from,row_bytes);
                else for(size_t x=0;x<row_bytes;x+=4){
                        const int mask=masked?frozen_mask[size_t(y-top)*size_t(right-left)+x/4]:0;
                    const int transparency=mask+anti;
                    if(transparency>=255||(masked&&anti>0&&transparency>=254))continue;
                    if(transparency==0){std::memcpy(destination+x,from+x,4);continue;}
                    for(int c=0;c<4;++c)destination[x+c]=uint8_t(
                        (destination[x+c]*(transparency+1)+from[x+c]*(256-transparency))>>8);
                }
                // Original grp_copy copies an associated mask only when
                // both pages have one. An ordinary RGB caption/button
                // strip must retain the destination's existing silhouette.
                if(d.antidata&&masked)for(int64_t x=left;x<right;++x){
                    const size_t pixel=size_t(int64_t(dy)+y)*d.w+size_t(int64_t(dx)+x);
                    auto& mask=get(d.antidata);
                    const int source_mask=masked?
                        frozen_mask[size_t(y-top)*size_t(right-left)+size_t(x-left)]:0;
                    // Original 0x47CC50 unions associated masks separately
                    // from the fourth RGB storage byte and uniform fade.
                    const uint8_t value=copying?uint8_t(source_mask):
                        uint8_t(mask.rgba[pixel*4]*(source_mask+1)>>8);
                    mask.rgba[pixel*4]=mask.rgba[pixel*4+1]=mask.rgba[pixel*4+2]=value;
                }
            }
        }return "OK";
    }
    if(op=="fill"||op=="extfill"||op=="mulfill"){int id,x,y,w,h;uint32_t color;
        require(bool(in>>id>>x>>y>>w>>h>>color),"fill arguments");
        int amount=255;if(op=="extfill")require(bool(in>>amount),"extended fill amount");
        amount=std::clamp(amount,0,255);
        if(w<=0||h<=0)return "OK";
        Page& p=get(id);
        const bool multiply=op=="mulfill";
        const uint8_t value[4]={uint8_t(color),
            p.depth==8?uint8_t(color):uint8_t(color>>8),
            p.depth==8?uint8_t(color):uint8_t(color>>16),255};
        const int64_t right=std::min(int64_t(p.w),int64_t(x)+w);
        const int64_t bottom=std::min(int64_t(p.h),int64_t(y)+h);
        if(!multiply&&amount==255){
            const int64_t left=std::max(x,0),top=std::max(y,0);
            if(left>=right||top>=bottom)return "OK";
            uint32_t packed;std::memcpy(&packed,value,4);
            for(int64_t yy=top;yy<bottom;++yy)
                std::fill_n(reinterpret_cast<uint32_t*>(p.rgba.data())+size_t(yy)*p.w+left,
                    size_t(right-left),packed);
            return "OK";
        }
        for(int64_t yy=std::max(y,0);yy<bottom;++yy)
            for(int64_t xx=std::max(x,0);xx<right;++xx){
                auto at=(size_t(yy)*p.w+size_t(xx))*4;
                for(int c=0;c<4;++c){
                    if(multiply){
                        if(c<3)p.rgba[at+c]=uint8_t((p.rgba[at+c]*(value[c]+1))>>8);
                    }else p.rgba[at+c]=uint8_t(
                        (p.rgba[at+c]*(255-amount)+value[c]*(amount+1))>>8);
                }
            }return "OK";
    }
    if(op=="pixel"){int id,x,y;require(bool(in>>id>>x>>y),"pixel arguments");Page& p=get(id);
        require(x>=0 && y>=0 && x<p.w && y<p.h,"pixel bounds");
        size_t at=(size_t(y)*p.w+x)*4;std::ostringstream out;out<<"OK";
        for(int c=0;c<3;++c)out<<' '<<int(p.rgba[at+c]);
        out<<' '<<int(alpha_at(p,at/4));return out.str();
    }
    if(op=="set"||op=="get"){
        int id,x,y;require(bool(in>>id>>x>>y),"point arguments");Page& p=get(id);
        uint32_t color=0;if(op=="set")require(bool(in>>color),"point color");
        if(x<0||y<0||x>=p.w||y>=p.h)return op=="get"?"OK -1":"OK";
        const size_t at=(size_t(y)*p.w+x)*4;
        if(op=="get")return "OK "+std::to_string(p.depth==8?p.rgba[at]:
            uint32_t(p.rgba[at])+(uint32_t(p.rgba[at+1])<<8)+(uint32_t(p.rgba[at+2])<<16));
        p.rgba[at]=uint8_t(color);p.rgba[at+1]=p.depth==8?uint8_t(color):uint8_t(color>>8);
        p.rgba[at+2]=p.depth==8?uint8_t(color):uint8_t(color>>16);p.rgba[at+3]=255;
        return "OK";
    }
    if(op=="smoothframe"){
        int source,sx,sy,nx,ny,w,h,dest,anti;
        require(bool(in>>source>>sx>>sy>>nx>>ny>>w>>h>>dest>>anti),"smooth frame arguments");
        const Page s=get(source);auto& d=get(dest);
        require(s.depth==32&&d.depth==32&&w==d.w&&h==d.h&&anti>=0&&anti<=255,"smooth frame format");
        require(sx>=0&&sy>=0&&nx>=0&&ny>=0&&int64_t(sx)+w<=s.w&&int64_t(nx)+w<=s.w&&
            int64_t(sy)+h<=s.h&&int64_t(ny)+h<=s.h,"smooth animation frame outside page");
        for(int y=0;y<h;++y)for(int x=0;x<w;++x){
            const size_t from=(size_t(sy+y)*s.w+sx+x)*4,next=(size_t(ny+y)*s.w+nx+x)*4;
            const size_t to=(size_t(y)*w+x)*4;
            for(int c=0;c<4;++c)d.rgba[to+c]=anti==255?s.rgba[from+c]:
                uint8_t((s.rgba[from+c]*(anti+1)+s.rgba[next+c]*(256-anti))>>8);
            if(d.antidata){auto& out=get(d.antidata);
                const auto a=255-alpha_at(s,from/4),b=255-alpha_at(s,next/4);
                const auto value=anti==255?a:(a*(anti+1)+b*(256-anti))>>8;
                out.rgba[to]=out.rgba[to+1]=out.rgba[to+2]=uint8_t(value);}
        }
        return "OK";
    }
    if(op=="thumbrect"){
        int source,sx,sy,w,h,dest,dx,dy,dw,dh;
        require(bool(in>>source>>sx>>sy>>w>>h>>dest>>dx>>dy>>dw>>dh),"thumbnail rectangle arguments");
        const Page frozen=get(source);auto& output=get(dest);
        if(frozen.depth!=32||output.depth!=32)return "OK";
        require(w>0&&h>0&&dw>0&&dh>0&&dw<=32768&&dh<=32768,"thumbnail dimensions");
        require(sx>=0&&sy>=0&&int64_t(sx)+w<=frozen.w&&int64_t(sy)+h<=frozen.h,
            "thumbnail source rectangle outside page");
        const int cw=w/dw,ch=h/dh;
        const int offsets[4][2]={{cw/5,ch/5},{2*cw/5,3*ch/5},{3*cw/5,4*ch/5},{4*cw/5,2*ch/5}};
        for(int y=std::max(0,-dy);y<std::min<int64_t>(dh,int64_t(output.h)-dy);++y)
            for(int x=std::max(0,-dx);x<std::min<int64_t>(dw,int64_t(output.w)-dx);++x){
                const int px=sx+int(int64_t(w)*x/dw),py=sy+int(int64_t(h)*y/dh);
                const size_t to=(size_t(y+dy)*output.w+size_t(x+dx))*4;
                for(int c=0;c<3;++c){
                    int total=0;
                    for(const auto& shift:offsets)
                        total+=frozen.rgba[(size_t(py+shift[1])*frozen.w+size_t(px+shift[0]))*4+c];
                    output.rgba[to+c]=uint8_t(total/4);
                }
                if(output.antidata){auto& mask=get(output.antidata);
                    mask.rgba[to]=mask.rgba[to+1]=mask.rgba[to+2]=0;}
            }
        return "OK";
    }
    if(op=="tablecopy"){
        int source,sx,sy,w,h,dest,dx,dy,control;
        require(bool(in>>source>>sx>>sy>>w>>h>>dest>>dx>>dy>>control),"table copy arguments");
        auto& s=get(source);auto& d=get(dest);const Page mask=get(control);
        require(s.depth==32&&d.depth==32&&mask.depth==8,"table copy depth");
        require(mask.w==s.w&&mask.h==s.h,"table copy mask dimensions");
        std::array<uint8_t,256> table{};
        for(auto& value:table){uint32_t bits;require(bool(in>>bits),"table copy lookup");
            int64_t signed_value=bits<0x80000000u?bits:int64_t(bits)-0x100000000ll;
            value=uint8_t(std::clamp<int64_t>(signed_value,0,255));}
        const int64_t left=std::max({int64_t(0),-int64_t(sx),-int64_t(dx)});
        const int64_t top=std::max({int64_t(0),-int64_t(sy),-int64_t(dy)});
        const int64_t right=std::min({int64_t(w),int64_t(s.w)-sx,int64_t(d.w)-dx});
        const int64_t bottom=std::min({int64_t(h),int64_t(s.h)-sy,int64_t(d.h)-dy});
        // Keep live source traversal, as the original row blender does.
        for(int64_t y=top;y<bottom;++y)for(int64_t x=left;x<right;++x){
            const size_t from=(size_t(sy+y)*s.w+sx+x)*4,to=(size_t(dy+y)*d.w+dx+x)*4;
            const unsigned anti=table[mask.rgba[from]];
            if(anti<255)for(int c=0;c<4;++c)
                d.rgba[to+c]=anti?uint8_t((d.rgba[to+c]*(anti+1)+s.rgba[from+c]*(256-anti))>>8):s.rgba[from+c];
            if(d.antidata){auto& dm=get(d.antidata);const unsigned previous=dm.rgba[to];
                const auto value=uint8_t((previous*(anti+1))>>8);
                dm.rgba[to]=dm.rgba[to+1]=dm.rgba[to+2]=value;}
        }
        return "OK";
    }
    if(op=="fuchidori"){
        int source,dest,control,dx,dy;
        require(bool(in>>source>>dest>>control>>dx>>dy),"fuchidori arguments");
        auto& s=get(source);auto& d=get(dest);auto& mask=get(control);
        require(s.depth==32&&d.depth==32&&mask.depth==8,"fuchidori depth");
        std::array<uint32_t,256> table{};
        for(auto& value:table)require(bool(in>>value),"fuchidori lookup");
        const int64_t width=std::min(s.w,mask.w),height=std::min(s.h,mask.h);
        const bool alpha=s.antidata&&d.antidata;
        for(int64_t y=std::max(0,-dy);y<std::min<int64_t>(height,int64_t(d.h)-dy);++y)
            for(int64_t x=std::max(0,-dx);x<std::min<int64_t>(width,int64_t(d.w)-dx);++x){
                const size_t from=(size_t(y)*s.w+x)*4,to=(size_t(y+dy)*d.w+size_t(x+dx))*4;
                const uint32_t entry=table[mask.rgba[(size_t(y)*mask.w+x)*4]];
                const unsigned code=entry>>24,group=code&0xc0,sub=code&0x30,amount=code&15;
                bool source_alpha=false;
                if(code==0){for(int c=0;c<4;++c)d.rgba[to+c]=s.rgba[from+c];source_alpha=true;}
                else if(code==128||group==128){
                    for(int c=0;c<3;++c)d.rgba[to+c]=uint8_t(entry>>((2-c)*8));
                    if(alpha){auto& a=get(s.antidata);auto& b=get(d.antidata);
                        const auto value=code==128?uint8_t(unsigned(a.rgba[from])*b.rgba[to]>>8):
                            std::min({a.rgba[from],b.rgba[to],uint8_t(amount*17)});
                        b.rgba[to]=b.rgba[to+1]=b.rgba[to+2]=value;}
                }else if(group==0||group==64){
                    for(int c=0;c<3;++c){
                        const int base=group==0?s.rgba[from+c]:d.rgba[to+c];
                        const int color=(entry>>((2-c)*8))&255;
                        if(sub==16)d.rgba[to+c]=uint8_t(std::min(255,base+color));
                        else if(sub==32)d.rgba[to+c]=uint8_t(std::max(0,base-color));
                        else if(sub==0&&group==0)
                            d.rgba[to+c]=uint8_t((s.rgba[from+c]*(15-amount)+d.rgba[to+c]*amount)/15);
                    }
                    source_alpha=group==0&&sub!=48;
                }
                if(alpha&&source_alpha){auto& a=get(s.antidata);auto& b=get(d.antidata);
                    b.rgba[to]=b.rgba[to+1]=b.rgba[to+2]=a.rgba[from];}
            }
        return "OK";
    }
    if(op=="cachebmp"){
        int id;require(bool(in>>id),"cache bitmap page");const auto& p=get(id);
        require(p.depth==32,"cache bitmap requires a color page");
        const uint32_t stride=uint32_t((p.w*3+3)&~3),bytes=stride*uint32_t(p.h),size=54+bytes;
        std::vector<uint8_t> data(size,0);
        auto u16=[&](size_t at,uint16_t value){data[at]=uint8_t(value);data[at+1]=uint8_t(value>>8);};
        auto u32=[&](size_t at,uint32_t value){for(int i=0;i<4;++i)data[at+i]=uint8_t(value>>(i*8));};
        data[0]='B';data[1]='M';u32(2,size);u32(10,54);u32(14,40);
        u32(18,uint32_t(p.w));u32(22,uint32_t(p.h));u16(26,1);u16(28,24);u32(34,bytes);
        for(int y=0;y<p.h;++y)for(int x=0;x<p.w;++x){
            const size_t from=(size_t(y)*p.w+x)*4,to=54+size_t(p.h-y-1)*stride+size_t(x)*3;
            for(int c=0;c<3;++c)data[to+c]=p.rgba[from+2-c];
        }
        static const char hex[]="0123456789abcdef";std::string result="OK data ";
        result.reserve(result.size()+data.size()*2);
        for(auto value:data){result+=hex[value>>4];result+=hex[value&15];}
        return result;
    }
    if(op=="thumbnail"){
        int source,dest;require(bool(in>>source>>dest),"thumbnail arguments");
        const Page frozen=get(source);auto& output=get(dest);
        require(frozen.depth==32&&output.depth==32,"thumbnail depth");
        int cw=frozen.w/output.w,ch=frozen.h/output.h;
        const int offsets[4][2]={{cw/5,ch/5},{2*cw/5,3*ch/5},
            {3*cw/5,4*ch/5},{4*cw/5,2*ch/5}};
        for(int y=0;y<output.h;++y)for(int x=0;x<output.w;++x){
            const int sx=frozen.w*x/output.w,sy=frozen.h*y/output.h;
            const size_t to=(size_t(y)*output.w+x)*4;
            for(int c=0;c<3;++c){
                int total=0;
                for(const auto& shift:offsets){
                    int px=std::min(frozen.w-1,sx+shift[0]),py=std::min(frozen.h-1,sy+shift[1]);
                    total+=frozen.rgba[(size_t(py)*frozen.w+px)*4+c];
                }
                output.rgba[to+c]=uint8_t(total/4);
            }
            output.rgba[to+3]=255;
        }
        return "OK";
    }
    if(op=="sepia"){
        int id,x,y,w,h,amount= -1;uint32_t low,high;
        require(bool(in>>id>>x>>y>>w>>h>>low>>high),"sepia arguments");in>>amount;
        Page& p=get(id);require(p.depth==32 && w>=0&&h>=0,"sepia page/depth");
        const int right=int(std::min(int64_t(p.w),int64_t(x)+w));
        const int bottom=int(std::min(int64_t(p.h),int64_t(y)+h));
        for(int yy=std::max(0,y);yy<bottom;++yy)for(int xx=std::max(0,x);xx<right;++xx){
            const size_t at=(size_t(yy)*p.w+xx)*4;
            const int luminance=2*p.rgba[at]+4*p.rgba[at+1]+p.rgba[at+2]+1;
            for(int c=0;c<3;++c){
                int tint=(int((high>>(c*8))&255)*luminance+
                    int((low>>(c*8))&255)*(2049-luminance))/2048;
                if(amount>=0)tint=(tint*(256-amount)+p.rgba[at+c]*(amount+1))/256;
                p.rgba[at+c]=uint8_t(tint);
            }
        }return "OK";
    }
    if(op=="alphatable"){
        int source,dest;require(bool(in>>source>>dest),"alpha table pages");
        auto& s=get(source);auto& d=get(dest);
        require(s.depth==8 && d.depth==8 && s.w==d.w&&s.h==d.h,"alpha table formats");
        std::array<uint8_t,256> table{};
        for(auto& v:table){int value;require(bool(in>>value),"alpha lookup truncated");v=uint8_t(std::clamp(value,0,255));}
        for(size_t at=0;at<d.rgba.size();at+=4){
            const auto value=table[s.rgba[at]];
            d.rgba[at]=d.rgba[at+1]=d.rgba[at+2]=value;d.rgba[at+3]=255;
        }return "OK";
    }
    if(op=="glyphs"){
        int id,x,y,height,half;uint32_t color,shadow;
        require(bool(in>>id>>x>>y>>height>>half>>color>>shadow),"glyph arguments");
        Page& p=get(id);require(p.depth==32&&height>0&&height<=256&&half>0&&half<=256,"glyph page/font");
        std::string face,advances,message;
        std::getline(in,face);std::getline(in,face);std::getline(in,advances);std::getline(in,message,'\0');
        const auto unicode=wide(message),font_face=wide(face);
        std::istringstream metrics(advances);std::vector<int> dx;int total=0,step;
        for(size_t at=0;at<unicode.size();++at){
            require(bool(metrics>>step)&&step>=0&&step<=512,"glyph advance missing");
            if(unicode[at]>=0xd800&&unicode[at]<=0xdbff&&at+1<unicode.size()){
                dx.push_back(0);++at;
            }
            dx.push_back(step);total+=step;
        }
        const int outline=std::min(8,int(shadow>>24)),pad=outline+2;
        const int rw=total+2*pad,rh=height+2*pad+8;
        require(rw>0&&size_t(rw)*rh<=16*1024*1024,"glyph raster budget");
        const int scale=3,sw=rw*scale,sh=rh*scale;
        HDC dc=CreateCompatibleDC(nullptr);require(dc!=nullptr,"glyph GDI context");
        HFONT font=CreateFontW(-height*scale,0,0,0,FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,
            OUT_DEFAULT_PRECIS,CLIP_DEFAULT_PRECIS,ANTIALIASED_QUALITY,DEFAULT_PITCH,font_face.c_str());
        require(font!=nullptr,"glyph font");
        auto oldfont=SelectObject(dc,font);
        BITMAPINFO info={};info.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);
        info.bmiHeader.biWidth=sw;info.bmiHeader.biHeight=-sh;info.bmiHeader.biPlanes=1;
        info.bmiHeader.biBitCount=32;info.bmiHeader.biCompression=BI_RGB;
        void* bits=nullptr;HBITMAP bitmap=CreateDIBSection(dc,&info,DIB_RGB_COLORS,&bits,nullptr,0);
        require(bitmap&&bits,"glyph bitmap");auto oldbitmap=SelectObject(dc,bitmap);
        std::memset(bits,0,size_t(sw)*sh*4);SetTextColor(dc,RGB(255,255,255));SetBkMode(dc,TRANSPARENT);
        TEXTMETRICW tm{};GetTextMetricsW(dc,&tm);
        for(auto& advance:dx)advance*=scale;
        ExtTextOutW(dc,pad*scale,pad*scale-font_cell_offset(dc,tm,height*scale),
            0,nullptr,unicode.data(),UINT(unicode.size()),dx.data());
        const auto* raster=static_cast<const uint8_t*>(bits);
        std::vector<uint8_t> coverage(size_t(rw)*rh);
        for(int yy=0;yy<rh;++yy)for(int xx=0;xx<rw;++xx){
            int sum=0;
            for(int sy=0;sy<scale;++sy)for(int sx=0;sx<scale;++sx)
                sum+=raster[(size_t(yy*scale+sy)*sw+xx*scale+sx)*4];
            coverage[size_t(yy)*rw+xx]=uint8_t((sum+scale*scale/2)/(scale*scale));
        }
        auto paint=[&](int xx,int yy,int alpha,uint32_t rgb){
            int px=x+xx-pad,py=y+yy-pad;
            // CONSOLE_WROTE copies exactly this cell rectangle. Ink outside
            // it would survive the source's clear/copy and leak on black.
            if(!alpha||px<0||py<0||px>=p.w||py>=p.h||
               px<x||py<y||px>=x+total||py>=y+height)return;
            const size_t at=(size_t(py)*p.w+px)*4;
            if(p.antidata){
                auto& mask=get(p.antidata);
                int prior=255-mask.rgba[at],out=alpha+(prior*(255-alpha)+127)/255;
                for(int c=0;c<3;++c){
                    p.rgba[at+c]=uint8_t((((rgb>>(c*8))&255)*alpha+
                        (p.rgba[at+c]*prior*(255-alpha)+127)/255+out/2)/std::max(1,out));
                    mask.rgba[at+c]=uint8_t(255-out);
                }
            }else for(int c=0;c<3;++c)p.rgba[at+c]=uint8_t(
                (p.rgba[at+c]*(255-alpha)+((rgb>>(c*8))&255)*alpha+127)/255);
            p.rgba[at+3]=255;
        };
        if(outline)for(int yy=0;yy<rh;++yy)for(int xx=0;xx<rw;++xx){
            int alpha=0;
            for(int dy=-outline;dy<=outline;++dy)for(int ddx=-outline;ddx<=outline;++ddx){
                if(ddx*ddx+dy*dy>outline*outline||xx+ddx<0||yy+dy<0||xx+ddx>=rw||yy+dy>=rh)continue;
                alpha=std::max(alpha,int(coverage[size_t(yy+dy)*rw+xx+ddx]));
            }
            paint(xx,yy,alpha,shadow&0xffffff);
        }
        for(int yy=0;yy<rh;++yy)for(int xx=0;xx<rw;++xx)
            paint(xx,yy,coverage[size_t(yy)*rw+xx],color);
        SelectObject(dc,oldbitmap);SelectObject(dc,oldfont);
        DeleteObject(bitmap);DeleteObject(font);DeleteDC(dc);
        return "OK "+std::to_string(total)+" "+std::to_string(height);
    }
    if(op=="text"){
        int id,x,y,height,width,boxw,boxh,align;uint32_t color;
        require(bool(in>>id>>x>>y>>height>>width>>color>>boxw>>boxh>>align),"text arguments");
        Page& p=get(id);require(p.depth==32 && height>0&&height<=256,"text page/font");
        std::string face,message;std::getline(in,face);std::getline(in,face);std::getline(in,message,'\0');
        require(!message.empty(),"text content missing");
        const auto unicode=wide(message),font_face=wide(face);
        HDC dc=CreateCompatibleDC(nullptr);require(dc!=nullptr,"GDI device context");
        const int scale=3;
        HFONT font=CreateFontW(-height*scale,width*scale,0,0,FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,
            OUT_DEFAULT_PRECIS,CLIP_DEFAULT_PRECIS,ANTIALIASED_QUALITY,DEFAULT_PITCH,font_face.c_str());
        require(font!=nullptr,"GDI font");
        auto previous_font=SelectObject(dc,font);
        RECT measure={0,0,boxw>0?boxw*scale:32768,0};
        UINT flags=DT_NOPREFIX|DT_LEFT|DT_TOP|(boxw>0?DT_WORDBREAK:DT_SINGLELINE);
        DrawTextW(dc,unicode.c_str(),int(unicode.size()),&measure,flags|DT_CALCRECT);
        TEXTMETRICW tm{};GetTextMetricsW(dc,&tm);
        int rw=std::max(1,(int(measure.right)+scale-1)/scale),
            rh=std::max(1,(int(measure.bottom)-int(tm.tmInternalLeading)+scale-1)/scale);
        require(size_t(rw)*rh<=16*1024*1024,"text raster budget");
        if(boxw>0){if(align==0)x+=(boxw-rw)/2;else if(align==-2)x+=boxw-rw;}
        BITMAPINFO info={};info.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);
        const int sw=rw*scale,sh=rh*scale;
        info.bmiHeader.biWidth=sw;info.bmiHeader.biHeight=-sh;
        info.bmiHeader.biPlanes=1;info.bmiHeader.biBitCount=32;info.bmiHeader.biCompression=BI_RGB;
        void* bits=nullptr;HBITMAP bitmap=CreateDIBSection(dc,&info,DIB_RGB_COLORS,&bits,nullptr,0);
        require(bitmap&&bits,"GDI raster bitmap");
        auto previous_bitmap=SelectObject(dc,bitmap);
        std::memset(bits,0,size_t(sw)*sh*4);SetTextColor(dc,RGB(255,255,255));SetBkMode(dc,TRANSPARENT);
        RECT rectangle={0,-font_cell_offset(dc,tm,height*scale),sw,sh};
        DrawTextW(dc,unicode.c_str(),int(unicode.size()),&rectangle,flags);
        const auto* coverage=static_cast<const uint8_t*>(bits);
        if(boxh>0){
            int first=sh,last=-1;
            for(int yy=0;yy<sh;++yy)for(int xx=0;xx<sw;++xx)
                if(coverage[(size_t(yy)*sw+xx)*4]>16){
                    first=std::min(first,yy);last=std::max(last,yy);
                }
            if(last>=first){
                const int top=first/scale,bottom=(last+scale)/scale;
                y+=(boxh-(bottom-top))/2-top;
            }else y+=(boxh-height)/2;
        }
        for(int yy=0;yy<rh;++yy)for(int xx=0;xx<rw;++xx){
            const int dx=x+xx,dy=y+yy;if(dx<0||dy<0||dx>=p.w||dy>=p.h)continue;
            int sum=0;
            for(int sy=0;sy<scale;++sy)for(int sx=0;sx<scale;++sx)
                sum+=coverage[(size_t(yy*scale+sy)*sw+xx*scale+sx)*4];
            const int alpha=(sum+scale*scale/2)/(scale*scale);
            const size_t at=(size_t(dy)*p.w+dx)*4;
            if(p.antidata&&alpha){
                auto& mask=get(p.antidata);
                for(int c=0;c<3;++c){
                    p.rgba[at+c]=uint8_t(color>>(c*8));
                    mask.rgba[at+c]=std::min(mask.rgba[at+c],uint8_t(255-alpha));
                }
            }else for(int c=0;c<3;++c)p.rgba[at+c]=uint8_t(
                (p.rgba[at+c]*(255-alpha)+((color>>(c*8))&255)*alpha+127)/255);
            if(alpha)p.rgba[at+3]=255;
        }
        SelectObject(dc,previous_bitmap);SelectObject(dc,previous_font);
        DeleteObject(bitmap);DeleteObject(font);DeleteDC(dc);
        return "OK "+std::to_string(rw)+" "+std::to_string(rh);
    }
    if(op=="png"){int id;require(bool(in>>id),"png arguments");png(get(id),path_arg(in));return "OK";}
    if(op=="modify"){
        int source,sx,sy,sw,sh,dest,dx,dy,dw,dh;double angle,xs,ys;
        require(bool(in>>source>>sx>>sy>>sw>>sh>>dest>>dx>>dy>>dw>>dh>>angle>>xs>>ys),
            "modify arguments");
        require(std::isfinite(angle)&&std::isfinite(xs)&&std::isfinite(ys)&&std::abs(xs)<=128&&
            std::abs(ys)<=128&&dw>=0&&dh>=0&&dw<=32768&&dh<=32768,"modify bounds");
        const Page frozen=get(source);
        composite(get(dest),frozen,sx,sy,sw,sh,dx+dw/2.0,dy+dh/2.0,xs,ys,angle,0,true,true,
            dx,dy,dw,dh);
        return "OK";
    }
    if(op=="paste"){
        int dest,page,sx,sy,w,h,anti,centered;double x,y,xs,ys,angle;
        require(bool(in>>dest>>page>>sx>>sy>>w>>h>>x>>y>>xs>>ys>>angle>>anti>>centered),
            "paste sprite arguments");
        require(std::isfinite(x)&&std::isfinite(y)&&std::isfinite(xs)&&std::isfinite(ys)&&
            std::isfinite(angle)&&std::abs(xs)<=128&&std::abs(ys)<=128&&anti>=0&&anti<=255,
            "paste geometry bounds");
        const Page source=get(page); // freeze source if pasting into its own page
        composite(get(dest),source,sx,sy,w,h,x,y,xs,ys,angle,anti,centered!=0);
        return "OK";
    }
    if(op=="render"||op=="present"||op=="presentview"||op=="presentzoom"||op=="renderfast"||op=="renderref"){
        int base;require(bool(in>>base),"render base");
        int kind=0;int32_t home_x=0,home_y=0;
        int zoom=0;
        if(op=="presentview"||op=="presentzoom")require(bool(in>>kind>>home_x>>home_y),"screen view arguments");
        if(op=="presentzoom")require(bool(in>>zoom)&&zoom>0&&zoom<=32768,"screen zoom arguments");
        std::string path;std::getline(in>>std::ws,path);
        require(!path.empty(),"render path");
        Page result=get(base);result.antidata=0;result.owns_alpha=false;result.depth=32;
        std::string row;
        while(std::getline(in,row)){
            if(row.empty())continue;
            std::istringstream item(row);
            int page,sx,sy,w,h,anti,centered;double x,y,xs,ys,angle;
            require(bool(item>>page>>sx>>sy>>w>>h>>x>>y>>xs>>ys>>angle>>anti>>centered),"render sprite row");
            require(std::isfinite(x)&&std::isfinite(y)&&std::isfinite(xs)&&std::isfinite(ys)&&
                std::isfinite(angle)&&std::abs(xs)<=128&&std::abs(ys)<=128,"render geometry bounds");
            composite(result,get(page),sx,sy,w,h,x,y,xs,ys,angle,anti,centered!=0,false,
                0,0,32768,32768,op!="renderref");
        }
        // A completed Majiro screen is opaque. Page alpha is meaningful for
        // later source operations, but exporting it causes Artemis to blend
        // the already-composited screen a second time over an old frame.
        for(size_t at=3;at<result.rgba.size();at+=4)result.rgba[at]=255;
        if(op=="presentview"||op=="presentzoom")
            result.rgba=screen_view.compose(result.rgba,result.w,result.h,home_x,home_y,
                static_cast<majiro::screen::Kind>(kind));
        if(zoom){
            const auto frozen=result.rgba;
            const int left=(result.w-zoom)/2,top=(result.h-zoom)/2;
            for(int y=0;y<result.h;++y)for(int x=0;x<result.w;++x){
                const size_t to=(size_t(y)*result.w+x)*4;
                if(x<left||y<top||x>=left+zoom||y>=top+zoom){
                    result.rgba[to]=result.rgba[to+1]=result.rgba[to+2]=0;
                }else{
                    const int sx=int(int64_t(x-left)*result.w/zoom),sy=int(int64_t(y-top)*result.h/zoom);
                    const size_t from=(size_t(sy)*result.w+sx)*4;
                    for(int c=0;c<3;++c)result.rgba[to+c]=frozen[from+c];
                }
                result.rgba[to+3]=255;
            }
        }
        if(op=="present"||op=="presentview"||op=="presentzoom"){
            if(last_width==result.w&&last_height==result.h&&last_presented==result.rgba)
                return "OK unchanged";
            png_fast(result,path);
            last_presented=result.rgba;last_width=result.w;last_height=result.h;
            return "OK changed";
        }
        if(op=="renderfast")png_fast(result,path);else png(result,path);
        return "OK";
    }
    if(op=="stats"){return "OK "+std::to_string(pages.size())+" "+std::to_string(used());}
    throw std::runtime_error("unknown pixel operation");
}
extern "C" __declspec(dllexport) char* __cdecl RuriPixels(const char* input) {
    std::lock_guard<std::mutex> lock(guard);std::string result;
    try{require(input!=nullptr,"null request");result=run(input);}
    catch(const std::exception& e){result="ERR "+std::string(e.what());}
    auto* output=static_cast<char*>(std::malloc(result.size()+1));
    if(!output)return nullptr;std::memcpy(output,result.c_str(),result.size()+1);return output;
}
extern "C" __declspec(dllexport) void __cdecl Delete(const char* output){std::free(const_cast<char*>(output));}
