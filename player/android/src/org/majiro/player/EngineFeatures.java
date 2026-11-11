package org.majiro.player;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/** Optional engine version/API hints. No executable hash or allowlist. */
public final class EngineFeatures {
    private EngineFeatures(){}
    private static boolean contains(byte[] source,byte[] value){
        outer:for(int at=0;at<=source.length-value.length;++at){
            for(int i=0;i<value.length;++i)if(source[at+i]!=value[i])continue outer;
            return true;
        }
        return false;
    }
    public static String[] from(byte[] source){
        ArrayList<String> features=new ArrayList<String>();
        if(source==null||!contains(source,"MajiroObj".getBytes(StandardCharsets.US_ASCII)))
            return new String[0];
        PeCode code=PeCode.read(source);
        if(code==null||(code.helper(0x45815f6d)<0&&code.ambiguousHashes.isEmpty())){
            // Some launchers store an ordinary PE interpreter as a resource.
            // Inspect its code in place; no extraction or execution is needed.
            int candidates=0;
            for(int at=2;at<source.length-64&&candidates<8;++at){
                if(source[at]!='M'||source[at+1]!='Z')continue;
                PeCode embedded=PeCode.read(source,at);
                if(embedded==null)continue;
                ++candidates;
                if(embedded.hasString("MajiroObj")&&embedded.helper(0x45815f6d)>=0){code=embedded;break;}
            }
        }
        if(code!=null&&code.hasString("MajiroObj")){
            for(int version=102;version<=105;++version)
                if(code.hasString("MajiroSavV1."+version+"\0"))features.add("save:"+version);
            // Public syscall IDs, resolved only through mapped code dispatch.
            // Bytes in resources, another PE or an overlay are not API hints.
            int[] identifiers={0x2bd1709d,0x7e0b8320,0x87a631a6,
                0xf63ed9ae,0x15d07df8,0xc102bcc3,0xa361d9f7,0x3670b283};
            for(int value:identifiers)if(code.selected(value)>=0)
                features.add("api:"+Integer.toHexString(value));
            int hook=code.helper(0x078a756e),release=code.helper(0xa93c9856);
            List<Integer> hooks=code.calls(hook),releases=code.calls(release);
            if(hooks.size()==4&&hooks.equals(releases))
                features.add("event:release_registers_hook");
            int compare=code.helper(0x45815f6d);
            if(compare>=0){
                features.add("engine:api-dispatch");
                boolean custom=code.customStringCompare(compare);
                features.add(custom?"string:latin_fullwidth":"string:cp932");
                features.add(code.hasControlFormat()?"text:x_event":"text:x_no_event");
            }
            int search=code.helper(0x69db4512);
            if(search>=0&&code.matches(search,new byte[]{(byte)0x81,(byte)0xec,0,2,0,0}))
                features.add("string:search_case_sensitive");
            int warningWrapper=code.selected(0x253982f4);
            if(warningWrapper>=0){
                Set<Integer> flags=new HashSet<Integer>();
                for(int warning:new int[]{warningWrapper,code.helper(0x253982f4)}){
                    if(warning<0)continue;
                    for(int at=warning,end=code.functionEnd(warning,512);at<end;){
                        int size=code.length(at);if(size<=0)break;
                        int op=source[at]&255;
                        if(op==0xe9||op==0xeb)break;
                        if(size==2&&op==0x6a
                                &&(source[at+1]==0x25||source[at+1]==0x35))flags.add((int)source[at+1]);
                        if(size==5&&op>=0xb8&&op<=0xbf
                                &&(code.i32(at+1)==0x25||code.i32(at+1)==0x35))flags.add(code.i32(at+1));
                        at+=size;
                    }
                }
                if(flags.size()==1)features.add("dialog:warning_retrycancel:"+flags.iterator().next());
            }
            Boolean history=code.historySuppressesMessage();
            if(history!=null)features.add(history?"text:history_suppresses_message":"text:history_retains_message");
            int cursor=code.selected(0xb84c9953);
            if(cursor>=0){
                boolean reset=false;
                for(int target:code.wrapperCalls(cursor)){
                    int end=code.functionEnd(target,512);
                    for(int at=target;at<end;){
                        int size=code.length(at);if(size<=0)break;
                        int op=source[at]&255;
                        if((op==0x3d&&size==5&&code.i32(at+1)==-10000)
                                ||(op==0x81&&size>=6&&((source[at+1]>>3)&7)==7
                                &&code.i32(at+size-4)==-10000))reset=true;
                        at+=size;
                    }
                }
                features.add(reset?"screen:cursor_reset_sentinel":"screen:cursor_literal_coordinates");
            }
            String xConversion=code.cursorConversion(0xefd49bc9);
            String yConversion=code.cursorConversion(0x72db7abf);
            if(xConversion!=null&&xConversion.equals(yConversion))
                features.add("screen:integer_conversion:"+xConversion);
            Boolean arrayStride=code.legacyArrayStride();
            if(arrayStride!=null)features.add(arrayStride?"array:3d_legacy_stride":"array:3d_row_major");
            String audioOrder=code.audioExtensionOrder();
            if(audioOrder!=null)features.add("audio:extensions:"+audioOrder);
            if(code.constantLicenseResult())features.add("external:license:constant_zero");
            int voice=code.helper(0x90d5298a);
            if(voice>=0){
                Set<Integer> functions=code.reachable(voice,2);
                boolean exact=false,prefix=false;
                for(int function:functions){
                    int end=code.functionEnd(function,768);
                    for(int at=function;at<end;){
                        int size=code.length(at);
                        if(size<=0||at+size>end)break;
                        if(size==5&&(source[at]&255)==0x68){
                            int pointer=code.offset(Integer.toUnsignedLong(code.i32(at+1)));
                            if(pointer>=0&&code.matches(pointer,new byte[]{35,35,110,119,35,35,0}))
                                exact=true;
                        }
                        // mov r32,0x2323; cmp word ptr [filename],r16.
                        if(size==5&&(source[at]&255)>=0xb8&&(source[at]&255)<=0xbf
                                &&code.i32(at+1)==0x2323&&at+8<=end
                                &&(source[at+5]&255)==0x66&&(source[at+6]&255)==0x39)
                            prefix=true;
                        at+=size;
                    }
                }
                if(prefix)features.add("voice:marker_prefix");
                else if(exact)features.add("voice:marker_exact");
                else features.add("voice:marker_none");
            }
        }
        if(code!=null)for(int hash:new TreeSet<Integer>(code.ambiguousHashes))
            features.add("engine:ambiguous-dispatch:"+Integer.toHexString(hash));
        return features.toArray(new String[features.size()]);
    }

    /** Read only the bounded dispatch/wrapper code needed by the hints above. */
    private static final class PeCode {
        // Public ABI IDs queried by this detector. Keep the index bounded
        // even if an untrusted code section is filled with random CMP/Jcc.
        static final int[] FEATURE_DISPATCH_IDS={
            0x45815f6d,0x2bd1709d,0x7e0b8320,0x87a631a6,0xf63ed9ae,
            0x15d07df8,0xc102bcc3,0xa361d9f7,0x3670b283,0x078a756e,
            0xa93c9856,0x69db4512,0x253982f4,0xb84c9953,0xefd49bc9,
            0x72db7abf,0x90d5298a,0xd4066e31,0x05ea6e4d,0x45ad5f4d};
        static {Arrays.sort(FEATURE_DISPATCH_IDS);}
        final byte[] data;
        final long base;
        final int[][] sections;
        final Map<Integer,Integer> selections=new HashMap<Integer,Integer>();
        final Set<Integer> ambiguousHashes=new HashSet<Integer>();
        Map<Integer,Set<Integer>> dispatchTargets;
        PeCode(byte[] bytes,long imageBase,int[][] ranges){data=bytes;base=imageBase;sections=ranges;}
        int i32(int at){return(data[at]&255)|(data[at+1]&255)<<8|(data[at+2]&255)<<16|(data[at+3]&255)<<24;}
        int u16(int at){return(data[at]&255)|(data[at+1]&255)<<8;}
        static PeCode read(byte[] bytes){
            return read(bytes,0);
        }
        static PeCode read(byte[] bytes,int origin){
            try{
                if(origin<0||origin>bytes.length-64||bytes[origin]!='M'||bytes[origin+1]!='Z')return null;
                PeCode probe=new PeCode(bytes,0,new int[0][]);
                int relative=probe.i32(origin+60);
                if(relative<64||relative>bytes.length-origin-96)return null;
                int pe=origin+relative;
                if(probe.i32(pe)!=0x4550||probe.u16(pe+4)!=0x14c)return null;
                int count=probe.u16(pe+6),optional=probe.u16(pe+20),start=pe+24+optional;
                if(count<1||count>96||optional<96||probe.u16(pe+24)!=0x10b
                        ||start<0||start>bytes.length-count*40)return null;
                int[][] sections=new int[count][];
                long resource=optional>=120?Integer.toUnsignedLong(probe.i32(pe+136)):0;
                long resourceSize=optional>=120?Integer.toUnsignedLong(probe.i32(pe+140)):0;
                for(int i=0;i<count;++i){
                    int at=start+i*40,size=probe.i32(at+16),raw=probe.i32(at+20),rva=probe.i32(at+12);
                    int virtual=probe.i32(at+8);
                    if(size<0||raw<0||raw>bytes.length-origin-size||rva<0||virtual<0
                            ||(long)rva+Math.max(size,virtual)>0x100000000L)return null;
                    int flags=probe.i32(at+36);
                    if(bytes[at]=='.'&&bytes[at+1]=='t'&&bytes[at+2]=='e'
                            &&bytes[at+3]=='x'&&bytes[at+4]=='t'&&bytes[at+5]==0)
                        flags|=0x20;
                    boolean resources=resourceSize>0&&rva>=resource&&rva-resource<resourceSize;
                    resources|=bytes[at]=='.'&&bytes[at+1]=='r'&&bytes[at+2]=='s'
                        &&bytes[at+3]=='r'&&bytes[at+4]=='c'&&bytes[at+5]==0;
                    // File-alignment padding is not part of the declared code
                    // extent. Keep it readable as data, but never as dispatch.
                    int codeSize=virtual==0?size:Math.min(size,virtual);
                    sections[i]=new int[]{origin+raw,size,rva,flags,Math.max(size,virtual),resources?1:0,codeSize};
                }
                return new PeCode(bytes,Integer.toUnsignedLong(probe.i32(pe+52)),sections);
            }catch(IndexOutOfBoundsException malformed){return null;}
        }
        boolean hasString(String text){
            byte[] value=text.getBytes(StandardCharsets.US_ASCII);
            for(int[] s:sections)if(s[5]==0)
                for(int at=s[0],end=s[0]+s[1]-value.length;at<=end;++at)
                    if(matches(at,value))return true;
            return false;
        }
        boolean mapped(long va){
            long rva=va-base;
            for(int[] s:sections)if(rva>=s[2]&&rva-s[2]<s[4])return true;
            return false;
        }
        boolean executable(int at){
            for(int[] s:sections)if((s[3]&0x20000020)!=0&&at>=s[0]&&at-s[0]<s[6])return true;
            return false;
        }
        int offset(long va){
            long rva=va-base;
            for(int[] s:sections)if(rva>=s[2]&&rva-s[2]<s[1])return s[0]+(int)(rva-s[2]);
            return -1;
        }
        long address(int at){
            for(int[] s:sections)if(at>=s[0]&&at-s[0]<s[1])return base+s[2]+at-s[0];
            return -1;
        }
        int relative(int at,int size,int displacement){
            long va=address(at);return va<0?-1:offset(va+size+displacement);
        }
        boolean matches(int at,byte[] value){
            if(at<0||at>data.length-value.length)return false;
            for(int i=0;i<value.length;++i)if(data[at+i]!=value[i])return false;
            return true;
        }
        // Flags after CMP(value,value): ZF=PF=1, CF=SF=OF=0.
        boolean equalBranch(int condition){
            return condition==1||condition==3||condition==4||condition==6
                ||condition==9||condition==10||condition==13||condition==14;
        }
        int selected(int hash){
            Integer cached=selections.get(hash);
            if(cached!=null)return cached;
            if(dispatchTargets==null)indexDispatch();
            Set<Integer> targets=dispatchTargets.get(hash);
            int result=targets!=null&&targets.size()==1?targets.iterator().next():-1;
            if(targets!=null&&targets.size()>1)ambiguousHashes.add(hash);
            selections.put(hash,result);return result;
        }
        void indexDispatch(){
            // One immutable input, one code pass. All queried public IDs use
            // the same bounded comparison/branch rules and target sets.
            dispatchTargets=new HashMap<Integer,Set<Integer>>();
            for(int[] s:sections){
                if((s[3]&0x20000020)==0)continue;
                for(int at=s[0];at+6<=s[0]+s[6];++at){
                    int op=data[at]&255,immediate=-1;
                    if(op==0x3d)immediate=at+1;
                    else if(op==0x81&&(data[at+1]&255)>=0xf8)immediate=at+2;
                    if(immediate<0)continue;
                    int hash=i32(immediate);
                    if(Arrays.binarySearch(FEATURE_DISPATCH_IDS,hash)<0)continue;
                    int next=immediate+4;
                    for(int n=0;n<4&&next>=s[0]&&next+6<=s[0]+s[6];++n){
                        int branch=data[next]&255,condition=-1,size=0,delta=0;
                        if(branch>=0x70&&branch<=0x7f){condition=branch&15;size=2;delta=data[next+1];}
                        else if(branch==0x0f&&(data[next+1]&0xf0)==0x80){
                            condition=data[next+1]&15;size=6;delta=i32(next+2);
                        }
                        if(condition<0)break;
                        if(equalBranch(condition)){
                            int target=relative(next,size,delta);
                            if(executable(target))addDispatchTarget(hash,target);
                            break;
                        }
                        next+=size;
                        if(condition==5){if(executable(next))addDispatchTarget(hash,next);break;}
                    }
                }
            }
        }
        void addDispatchTarget(int hash,int target){
            Set<Integer> targets=dispatchTargets.get(hash);
            if(targets==null){targets=new HashSet<Integer>();dispatchTargets.put(hash,targets);}
            if(targets.size()<2)targets.add(target);
        }
        int helper(int hash){
            int at=selected(hash);
            if(at<0)return -1;
            for(int n=0;n<12&&at+5<=data.length;++n){
                int op=data[at]&255,size=length(at);
                if(size<=0||!executable(at+size-1))break;
                if(op==0xe8){
                    int target=relative(at,5,i32(at+1));
                    return executable(target)?target:-1;
                }
                if(op==0xc3||op==0xc2||op==0xe9||op==0xeb)break;
                at+=size;
            }
            return -1;
        }
        int bodyRoot(int hash){
            int at=selected(hash);
            return at>=0&&(data[at]&255)==0xe8?helper(hash):at;
        }
        List<Integer> wrapperCalls(int at){
            List<Integer> result=new ArrayList<Integer>();
            for(int n=0;n<64&&executable(at);++n){
                int size=length(at);if(size<=0||!executable(at+size-1))break;
                int op=data[at]&255;
                if(op==0xc3||op==0xc2||op==0xe9||op==0xeb||op==0xcc)break;
                if(op==0xe8){
                    int target=relative(at,5,i32(at+1));
                    if(executable(target))result.add(target);
                }
                at+=size;
            }
            return result;
        }
        List<Integer> calls(int at){
            List<Integer> result=new ArrayList<Integer>();
            if(at<0)return result;
            int end=functionEnd(at,768);
            while(at<end){
                int size=length(at);if(size<=0||at+size>end)break;
                if((data[at]&255)==0xe8){
                    int target=relative(at,5,i32(at+1));if(target>=0)result.add(target);
                }
                at+=size;
            }
            return result;
        }
        int functionEnd(int at,int bound){
            if(at<0)return 0;
            int end=Math.min(data.length,at+bound);
            for(int[] s:sections)if(at>=s[0]&&at-s[0]<s[1])end=Math.min(end,s[0]+s[6]);
            for(int p=at;p<end;){
                int size=length(p);
                if(size<=0||p+size>end)return p;
                int op=data[p]&255;
                if(op==0xc3||op==0xc2||op==0xcc)return p+size;
                if(op==0xe9||op==0xeb){
                    int target=relative(p,size,op==0xe9?i32(p+1):data[p+1]);
                    if(target<at||target>=end)return p+size;
                }
                p+=size;
            }
            return end;
        }
        Set<Integer> reachable(int root,int depth){
            Set<Integer> result=new HashSet<Integer>(),front=new HashSet<Integer>();
            if(root<0)return result;
            result.add(root);front.add(root);
            for(int i=0;i<depth&&result.size()<128;++i){
                Set<Integer> next=new HashSet<Integer>();
                for(int at:front)for(int target:calls(at))if(result.add(target))next.add(target);
                front=next;
            }
            return result;
        }
        boolean customStringCompare(int root){
            for(int at:reachable(root,1)){
                int end=functionEnd(at,256),buffers=0;
                for(int p=at;p<end;){
                    int size=length(p);if(size<=0)break;
                    if((data[p]&255)==0x68&&size==5&&i32(p+1)==0x7ff)++buffers;
                    p+=size;
                }
                if(buffers==2)return true;
            }
            return false;
        }
        String cursorConversion(int hash){
            Set<String> kinds=new HashSet<String>();
            int wrapper=selected(hash);
            if(wrapper<0)return null;
            Set<Integer> functions=new HashSet<Integer>();functions.add(wrapper);
            for(int root:wrapperCalls(wrapper))functions.addAll(reachable(root,2));
            for(int function:functions){
                boolean loadedInteger=false,divided=false,doubleDivision=false;
                int end=functionEnd(function,256);
                for(int at=function;at<end;){
                    int size=length(at);if(size<=0)break;
                    int op=data[at]&255,mod=size>=2?data[at+1]&255:0;
                    if(op==0xdb&&(mod>>3&7)==0&&(mod>>6)!=3)loadedInteger=true;
                    if(loadedInteger&&op==0xdc&&(mod>>3&7)==6&&(mod&0xc7)==5)
                        divided=true;
                    if(divided&&(op==0xe8||op==0xe9)&&size==5){
                        int conversion=relative(at,5,i32(at+1));
                        if(x87IntegerStore(conversion))kinds.add("x87");
                    }
                    if(size>=4&&op==0xf2&&(data[at+1]&255)==0x0f){
                        if((data[at+2]&255)==0x5e)doubleDivision=true;
                        if(doubleDivision&&(data[at+2]&255)==0x2c)kinds.add("sse32");
                    }
                    if(op==0xe9||op==0xeb)break;
                    at+=size;
                }
            }
            return kinds.size()==1?kinds.iterator().next():null;
        }
        boolean x87IntegerStore(int root){
            if(!executable(root))return false;
            for(int at=root,end=functionEnd(root,192);at<end;){
                int size=length(at);if(size<=0)break;
                // FISTP m32/m64 is the CRT float-to-integer conversion.
                int op=data[at]&255,mod=size>=2?data[at+1]&255:0;
                if((mod>>6)!=3&&((op==0xdb&&(mod>>3&7)==3)
                        ||(op==0xdf&&(mod>>3&7)==7)))return true;
                at+=size;
            }
            return false;
        }
        Boolean legacyArrayStride(){
            Set<Integer> helpers=new HashSet<Integer>();
            // Array operand flags encode their rank above bit 10. Resolve
            // calls immediately using that decoded rank, then prove the
            // callee's three modulo operations and symbolic index formula.
            for(int[] s:sections){
                if((s[3]&0x20000020)==0)continue;
                for(int at=s[0];at+8<=s[0]+s[6];++at){
                    if((data[at]&255)!=0xc1||(data[at+1]&0xf8)!=0xf8||data[at+2]!=11)continue;
                    for(int p=at+3,n=0;n<4&&p+5<=s[0]+s[6];++n){
                        int op=data[p]&255,size=length(p);
                        if(op==0xe8){
                            int target=relative(p,5,i32(p+1));
                            if(executable(target))helpers.add(target);
                            break;
                        }
                        if(size<=0||!((op>=0x50&&op<=0x57)||op==0x8b||op==0x89))break;
                        p+=size;
                    }
                }
            }
            Set<Boolean> kinds=new HashSet<Boolean>();
            for(int helper:helpers){
                Boolean kind=arrayIndexFormula(helper);
                if(kind!=null)kinds.add(kind);
            }
            return kinds.size()==1?kinds.iterator().next():null;
        }
        // A small symbolic calculation, not x86 execution: only register
        // data flow, dimensions and the final arithmetic are retained.
        // A/B/C are the normalized indices; D1/D2/D3 are dimension lengths.
        Boolean arrayIndexFormula(int root){
            Expr[] registers=new Expr[8];
            Map<Integer,Expr> stack=new HashMap<Integer,Expr>();
            int divisions=0,multiplies=0;
            for(int at=root,end=functionEnd(root,768);at<end;){
                int size=length(at);if(size<=0)break;
                int op=data[at]&255,m=size>=2?data[at+1]&255:0,reg=m>>3&7,rm=m&7;
                if(op==0xc3||op==0xc2){
                    if(divisions!=3||multiplies!=2||registers[0]==null)return null;
                    Expr a=Expr.variable(0),b=Expr.variable(1),c=Expr.variable(2);
                    Expr d2=Expr.variable(4),d3=Expr.variable(5);
                    Expr legacy=Expr.add(Expr.add(Expr.multiply(a,d2),Expr.multiply(b,d3)),c);
                    Expr standard=Expr.add(Expr.multiply(Expr.add(Expr.multiply(a,d2),b),d3),c);
                    if(Arrays.equals(registers[0].terms,legacy.terms))return Boolean.TRUE;
                    if(Arrays.equals(registers[0].terms,standard.terms))return Boolean.FALSE;
                    return null;
                }
                if(op==0xe9||op==0xeb)return null;
                // Positive normalized indices take the sign-check branch,
                // skipping the optional "remainder += dimension".
                if((op==0x79||op==0x7d)&&size==2&&(data[at+1]==2||data[at+1]==5)
                        &&length(at+2)==2&&(data[at+2]&255)==0x03){
                    if(data[at+1]==2||((data[at+4]&255)==0x89
                            &&(data[at+5]&0xc7)==0x45&&length(at+4)==3)){
                        at+=2+data[at+1];continue;
                    }
                }
                if(op==0xf7&&reg==7&&(m>>6)==3){
                    if(divisions>=3||registers[rm]==null
                            ||!Arrays.equals(registers[rm].terms,Expr.variable(5-divisions).terms))return null;
                    registers[0]=null;registers[2]=Expr.variable(2-divisions);++divisions;
                }else if(op==0x8b){
                    if((m>>6)==3)registers[reg]=registers[rm];
                    else if((m>>6)==1&&rm==5&&size==3)registers[reg]=stack.get((int)data[at+2]);
                    else if((m>>6)==1&&rm!=4&&size==3
                            &&(data[at+2]==8||data[at+2]==12||data[at+2]==16))
                        registers[reg]=Expr.variable(3+(data[at+2]-8)/4);
                    else registers[reg]=null;
                }else if(op==0x89&&(m>>6)==3)registers[rm]=registers[reg];
                else if(op==0x89&&(m>>6)==1&&rm==5&&size==3)stack.put((int)data[at+2],registers[reg]);
                else if(op==0x0f&&(data[at+1]&255)==0xaf){
                    int mod=data[at+2]&255,destination=mod>>3&7,source=mod&7;
                    Expr value=(mod>>6)==3?registers[source]:null;
                    if((mod>>6)==1&&source!=4&&size==4
                            &&(data[at+3]==12||data[at+3]==16))
                        value=Expr.variable(4+(data[at+3]-12)/4);
                    registers[destination]=Expr.multiply(registers[destination],value);
                    ++multiplies;
                }else if(op==0x03)registers[reg]=Expr.add(registers[reg],
                    (m>>6)==3?registers[rm]:(m>>6)==1&&rm==5&&size==3?stack.get((int)data[at+2]):null);
                else if(op==0x01&&(m>>6)==3)registers[rm]=Expr.add(registers[rm],registers[reg]);
                else if(op==0xa1)registers[0]=null;
                else if(op==0x8d)registers[reg]=null;
                else if((op>=0x58&&op<=0x5f)||(op>=0x40&&op<=0x4f)||(op>=0xb8&&op<=0xbf))
                    registers[op&7]=null;
                else if(op==0x99)registers[2]=null;
                else if(op==0xe8){registers[0]=null;registers[1]=null;registers[2]=null;}
                else if((op==0xc1||op==0x83||op==0x81)&&(m>>6)==3&&reg!=7)registers[rm]=null;
                at+=size;
            }
            return null;
        }
        List<Integer> flow(int root,int bound){
            TreeSet<Integer> seen=new TreeSet<Integer>();
            List<Integer> pending=new ArrayList<Integer>();
            if(!executable(root))return new ArrayList<Integer>();
            pending.add(root);
            while(!pending.isEmpty()&&seen.size()<1024){
                int at=pending.remove(pending.size()-1);
                while(at>=root&&at-root<bound&&executable(at)&&!seen.contains(at)){
                    int size=length(at);if(size<=0||!executable(at+size-1))break;
                    seen.add(at);
                    int op=data[at]&255,target=-1;
                    if(op==0xc3||op==0xc2||op==0xcc)break;
                    if(op>=0x70&&op<=0x7f)target=relative(at,2,data[at+1]);
                    else if(op==0x0f&&(data[at+1]&0xf0)==0x80)target=relative(at,6,i32(at+2));
                    else if(op==0xe9||op==0xeb){
                        at=relative(at,size,op==0xe9?i32(at+1):data[at+1]);continue;
                    }else if(op==0xff&&((data[at+1]>>3)&7)==4)break;
                    if(target>=root&&target-root<bound)pending.add(target);
                    at+=size;
                    if(seen.size()>=1024)break;
                }
            }
            return new ArrayList<Integer>(seen);
        }
        String extensionAt(int at){
            int op=data[at]&255,size=length(at),pointer=-1;
            if(size==5&&(op==0x68||op==0xa1||(op>=0xb8&&op<=0xbf)))
                pointer=offset(Integer.toUnsignedLong(i32(at+1)));
            else if(size==6&&op==0x8b&&(data[at+1]&0xc7)==5)
                pointer=offset(Integer.toUnsignedLong(i32(at+2)));
            if(matches(pointer,new byte[]{46,119,97,118,0}))return "wav";
            if(matches(pointer,new byte[]{46,111,103,103,0}))return "ogg";
            return null;
        }
        String audioExtensionOrder(){
            int root=selected(0x45ad5f4d); // $sound_is_exist
            if(root<0)return null;
            Set<Integer> seen=new HashSet<Integer>();
            List<Integer> pending=new ArrayList<Integer>();pending.add(root);
            Set<String> orders=new HashSet<String>();
            for(int depth=0;depth<4&&seen.size()<128;++depth){
                List<Integer> next=new ArrayList<Integer>();
                for(int function:pending){
                    if(seen.size()>=128)break;
                    if(!seen.add(function))continue;
                    List<Integer> body=flow(function,2048);
                    List<String> extensions=new ArrayList<String>();
                    List<Integer> predicates=new ArrayList<Integer>();
                    String extension=null;
                    for(int at:body){
                        String found=extensionAt(at);
                        if(found!=null)extension=found;
                        if((data[at]&255)!=0xe8)continue;
                        int call=relative(at,5,i32(at+1));
                        if(executable(call))next.add(call);
                        int following=at+5;
                        if(matches(following,new byte[]{(byte)0x83,(byte)0xc4}))
                            following+=3;
                        if(extension!=null&&executable(call)
                                &&matches(following,new byte[]{(byte)0x85,(byte)0xc0})){
                            extensions.add(extension);predicates.add(call);extension=null;
                        }
                    }
                    if(extensions.size()==2&&!extensions.get(0).equals(extensions.get(1))
                            &&predicates.get(0).equals(predicates.get(1)))
                        orders.add(extensions.get(0)+"-"+extensions.get(1));
                }
                if(!orders.isEmpty())break;
                pending=next;
            }
            return orders.size()==1?orders.iterator().next():null;
        }
        boolean constantLicenseResult(){
            int wrapper=selected(0x87a631a6);
            if(wrapper<0)return false;
            // Inlined fastcall wrapper: zero the integer-result argument,
            // push it through the VM helper and return to syscall dispatch.
            if(matches(wrapper,new byte[]{0x33,(byte)0xc9,(byte)0xe8})
                    &&executable(wrapper+11)&&(data[wrapper+7]&255)==0xe9)return true;
            for(int root:wrapperCalls(wrapper))for(int function:reachable(root,1)){
                for(int at=function,end=functionEnd(function,512);at<end;){
                    int size=length(at);if(size<=0)break;
                    if((data[at]&255)==0xe8&&at+6<=end&&(data[at+5]&255)==0x50){
                        int target=relative(at,5,i32(at+1));
                        if(executable(target+2)&&(matches(target,new byte[]{0x33,(byte)0xc0,(byte)0xc3})
                                ||matches(target,new byte[]{0x31,(byte)0xc0,(byte)0xc3})))return true;
                    }
                    at+=size;
                }
            }
            return false;
        }
        private static final class Expr {
            final int[] terms=new int[64];
            static Expr variable(int index){Expr result=new Expr();result.terms[1<<index]=1;return result;}
            static Expr add(Expr a,Expr b){
                if(a==null||b==null)return null;
                Expr result=new Expr();
                for(int i=0;i<64;++i)result.terms[i]=a.terms[i]+b.terms[i];
                return result;
            }
            static Expr multiply(Expr a,Expr b){
                if(a==null||b==null)return null;
                Expr result=new Expr();
                for(int i=0;i<64;++i)if(a.terms[i]!=0)
                    for(int j=0;j<64;++j)if(b.terms[j]!=0){
                        if((i&j)!=0)return null;
                        result.terms[i|j]+=a.terms[i]*b.terms[j];
                    }
                return result;
            }
        }
        boolean hasControlFormat(){
            byte[] format={120,37,115,0};
            for(int[] s:sections){
                if((s[3]&0x20000020)==0)continue;
                for(int at=s[0];at+5<=s[0]+s[6];++at){
                    if((data[at]&255)!=0x68)continue;
                    int pointer=offset(Integer.toUnsignedLong(i32(at+1)));
                    if(pointer<=0||data[pointer-1]!=0||!matches(pointer,format))continue;
                    // The control formats a string, drops its three sprintf
                    // arguments, obtains the event context, then submits one
                    // event argument. A literal or an isolated PUSH is not a
                    // control implementation.
                    int calls=0;boolean cleaned=false,one=false;
                    for(int p=at+5,end=Math.min(s[0]+s[6],at+80);p<end;){
                        int size=length(p);if(size<=0||p+size>end)break;
                        int op=data[p]&255;
                        if(op==0xc3||op==0xc2||op==0xe9||op==0xeb||op==0xcc)break;
                        if(calls==1&&matches(p,new byte[]{(byte)0x83,(byte)0xc4,12}))
                            cleaned=true;
                        if(calls==2&&((op==0x6a&&size==2&&data[p+1]==1)
                                ||(op==0xba&&size==5&&i32(p+1)==1)))one=true;
                        if(op==0xe8){
                            if(!executable(relative(p,5,i32(p+1))))break;
                            ++calls;
                            if(calls==3&&cleaned&&one)return true;
                        }
                        p+=size;
                    }
                }
            }
            return false;
        }
        int messageAppendEnd(int at){
            // In these engines the message destination is EDI and the copy
            // is REP MOVSD followed by REP MOVSB (including the terminator).
            if((data[at]&255)!=0xbf)return -1;
            boolean words=false;
            for(int p=at+5,end=functionEnd(at,128);p<end;){
                int size=length(p);if(size<=0)break;
                if(matches(p,new byte[]{(byte)0xf3,(byte)0xa5}))words=true;
                if(words&&matches(p,new byte[]{(byte)0xf3,(byte)0xa4}))return p+size;
                p+=size;
            }
            return -1;
        }
        Boolean historySuppressesMessage(){
            int disable=bodyRoot(0xd4066e31),getter=bodyRoot(0x05ea6e4d);
            if(disable<0||getter<0)return null;
            Set<Integer> slots=new HashSet<Integer>();
            for(int at=disable,end=functionEnd(disable,128);at<end;){
                int size=length(at);if(size<=0)break;
                int op=data[at]&255;
                if(size>=6&&(op==0x8b||op==0x89||op==0xff)
                        &&(data[at+1]&0xc7)==5)slots.add(i32(at+2));
                at+=size;
            }
            if(slots.size()!=1)return null;
            int depth=slots.iterator().next(),message=0;
            for(int at=getter,end=functionEnd(getter,64);at<end;){
                int size=length(at);if(size<=0)break;
                int op=data[at]&255;
                if(size==5&&(op==0x68||(op>=0xb8&&op<=0xbf))
                        &&mapped(Integer.toUnsignedLong(i32(at+1)))){message=i32(at+1);break;}
                at+=size;
            }
            if(message==0)return null;
            int references=0,guarded=0;
            for(int[] s:sections){
                if((s[3]&0x20000020)==0)continue;
                for(int at=s[0];at+5<=s[0]+s[6];++at){
                    int op=data[at]&255;
                    if(!(op==0x68||(op>=0xb8&&op<=0xbf))||i32(at+1)!=message)continue;
                    int appendEnd=messageAppendEnd(at);
                    if(appendEnd<0)continue;
                    ++references;
                    for(int check=Math.max(s[0],at-128);check+6<at;++check){
                        int cmp=data[check]&255,mod=data[check+1]&255;
                        if(!((cmp==0x39||cmp==0x3b)||(cmp==0x83&&(mod>>3&7)==7))
                                ||(mod&0xc7)!=5||i32(check+2)!=depth)continue;
                        int branch=check+(cmp==0x83?7:6),target=-1;
                        if(branch+2<=data.length&&(data[branch]&255)==0x75)
                            target=relative(branch,2,data[branch+1]);
                        else if(branch+6<=data.length&&(data[branch]&255)==0x0f&&(data[branch+1]&255)==0x85)
                            target=relative(branch,6,i32(branch+2));
                        if(target>=appendEnd&&target<=at+512){
                            // Reject a byte-pattern coincidence that cannot
                            // decode continuously through the append entry.
                            int p=branch+(data[branch]==0x75?2:6);
                            while(p<at){
                                int size=length(p),instruction=data[p]&255;
                                if(size<=0||instruction==0xc3||instruction==0xc2
                                        ||instruction==0xcc||instruction==0xe9||instruction==0xeb)break;
                                p+=size;
                            }
                            if(p==at){++guarded;break;}
                        }
                    }
                }
            }
            return references>0?Boolean.valueOf(guarded==references):null;
        }
        int length(int at){
            try{
                int p=at,width=4;
                while((data[p]&255)==0x66||(data[p]&255)==0xf2||(data[p]&255)==0xf3
                        ||(data[p]&255)==0x64||(data[p]&255)==0x65){
                    if((data[p]&255)==0x66)width=2;
                    if(++p-at>4)return 0;
                }
                int op=data[p++]&255,imm=0;boolean modrm=false;
                if(op==0x0f){
                    int second=data[p++]&255;
                    if(second>=0x80&&second<=0x8f)imm=4;
                    else if((second>=0x40&&second<=0x4f)||(second>=0x90&&second<=0x9f)
                            ||second==0xaf||second==0xb6||second==0xb7||second==0xbe||second==0xbf
                            ||second==0x6e||second==0x7e||second==0xe6
                            ||(second>=0x10&&second<=0x17)||(second>=0x28&&second<=0x2f)
                            ||(second>=0x50&&second<=0x5f))modrm=true;
                    else return 0;
                }else if((op>=0x40&&op<=0x5f)||(op>=0x90&&op<=0x9f)
                        ||op==0xc3||op==0xc9||op==0xcc||op==0xfc||op==0xfd
                        ||(op>=0xa4&&op<=0xa7)||(op>=0xaa&&op<=0xaf)){}
                else if(op>=0xb8&&op<=0xbf)imm=width;
                else if(op>=0xb0&&op<=0xb7)imm=1;
                else if(op==0x68||op==0xe8||op==0xe9)imm=4;
                else if(op==0x6a||op==0xeb||(op>=0x70&&op<=0x7f))imm=1;
                else if(op==0xc2)imm=2;
                else if(op>=0xa0&&op<=0xa3)imm=4;
                else if((op<=0x3d&&(op&7)==5)||op==0xa9)imm=width;
                else if((op<=0x3c&&(op&7)==4)||op==0xa8)imm=1;
                else if((op<=0x3b&&(op&7)<=3)||(op>=0x84&&op<=0x8f)
                        ||op==0x63||op==0x69||op==0x6b||op==0x80||op==0x81||op==0x83
                        ||op==0xc0||op==0xc1||op==0xc6||op==0xc7
                        ||(op>=0xd0&&op<=0xd3)||(op>=0xd8&&op<=0xdf)
                        ||op==0xf6||op==0xf7||op==0xfe||op==0xff)modrm=true;
                else return 0;
                if(modrm){
                    int m=data[p++]&255,mode=m>>6,rm=m&7;
                    if(mode!=3&&rm==4){int sib=data[p++]&255;if(mode==0&&(sib&7)==5)p+=4;}
                    if(mode==0&&rm==5)p+=4;
                    else if(mode==1)p+=1;
                    else if(mode==2)p+=4;
                    if(op==0x69||op==0x81||op==0xc7)imm=width;
                    else if(op==0x6b||op==0x80||op==0x83||op==0xc0||op==0xc1||op==0xc6)imm=1;
                    else if((op==0xf6||op==0xf7)&&((m>>3)&7)<=1)imm=op==0xf6?1:width;
                }
                p+=imm;return p<=data.length&&p-at<=15?p-at:0;
            }catch(IndexOutOfBoundsException malformed){return 0;}
        }
    }
}
