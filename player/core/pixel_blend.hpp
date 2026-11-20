#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#if defined(__ARM_NEON)
#include <arm_neon.h>
#elif defined(__SSE2__) || defined(_M_X64)
#include <emmintrin.h>
#endif

namespace majiro::raster {

// The Majiro channel weights sum to 257. The maximum numerator is 65535,
// so unsigned 16-bit lanes reproduce the scalar result without saturation.
inline uint8_t channel(uint8_t dst, uint8_t src, unsigned opacity) {
    return uint8_t((dst * (256 - opacity) + src * (1 + opacity)) >> 8);
}

#if defined(__SSE2__) || defined(_M_X64)
inline __m128i blend_words(__m128i dst, __m128i src, __m128i opacity) {
    // Equivalent modulo 65536 to dst*(256-opacity) + src*(1+opacity).
    const auto base = _mm_add_epi16(_mm_slli_epi16(dst, 8), src);
    const auto delta = _mm_mullo_epi16(_mm_sub_epi16(src, dst), opacity);
    return _mm_srli_epi16(_mm_add_epi16(base, delta), 8);
}

inline __m128i blend_block(__m128i dst, __m128i src, __m128i lo, __m128i hi) {
    const auto zero = _mm_setzero_si128();
    return _mm_packus_epi16(
        blend_words(_mm_unpacklo_epi8(dst, zero), _mm_unpacklo_epi8(src, zero), lo),
        blend_words(_mm_unpackhi_epi8(dst, zero), _mm_unpackhi_epi8(src, zero), hi));
}
#endif

inline void blend_constant(uint8_t* dst, const uint8_t* src, size_t bytes, unsigned anti) {
    size_t i = 0;
#if defined(__ARM_NEON)
    const auto dw = vdup_n_u8(uint8_t(anti + 1));
    const auto sw = vdup_n_u8(uint8_t(256 - anti)); // caller guarantees 1..254
    for (; i + 16 <= bytes; i += 16) {
        const auto d = vld1q_u8(dst + i);
        const auto s = vld1q_u8(src + i);
        const auto lo = vmlal_u8(vmull_u8(vget_low_u8(d), dw), vget_low_u8(s), sw);
        const auto hi = vmlal_u8(vmull_u8(vget_high_u8(d), dw), vget_high_u8(s), sw);
        vst1q_u8(dst + i, vcombine_u8(vshrn_n_u16(lo, 8), vshrn_n_u16(hi, 8)));
    }
#elif defined(__SSE2__) || defined(_M_X64)
    const auto opacity = _mm_set1_epi16(short(255 - anti));
    for (; i + 16 <= bytes; i += 16) {
        const auto d = _mm_loadu_si128(reinterpret_cast<const __m128i*>(dst + i));
        const auto s = _mm_loadu_si128(reinterpret_cast<const __m128i*>(src + i));
        _mm_storeu_si128(reinterpret_cast<__m128i*>(dst + i), blend_block(d, s, opacity, opacity));
    }
#endif
    for (; i < bytes; ++i) dst[i] = channel(dst[i], src[i], 255 - anti);
}

template<bool ExternalMask>
inline void blend_masked(uint8_t* dst, const uint8_t* src, const uint8_t* mask,
                         size_t pixels, unsigned anti) {
    size_t i = 0;
#if defined(__ARM_NEON) && defined(__aarch64__)
    const auto anti8 = vdup_n_u8(uint8_t(anti));
    for (; i + 8 <= pixels; i += 8) {
        auto s = vld4_u8(src + i * 4);
        if constexpr (ExternalMask) s.val[3] = vmvn_u8(vld4_u8(mask + i * 4).val[0]);
        auto opacity = vqsub_u8(s.val[3], anti8);
        if (anti) opacity = vbic_u8(opacity, vceq_u8(opacity, vdup_n_u8(1)));
        if (vmaxv_u8(opacity) == 0) continue;
        if (vminv_u8(opacity) != 255) {
            const auto sw = vaddl_u8(opacity, vdup_n_u8(1));
            const auto dw = vsubq_u16(vdupq_n_u16(256), vmovl_u8(opacity));
            const auto d = vld4_u8(dst + i * 4);
            for (int c = 0; c < 4; ++c) {
                const auto sum = vmlaq_u16(vmulq_u16(vmovl_u8(d.val[c]), dw),
                                           vmovl_u8(s.val[c]), sw);
                s.val[c] = vshrn_n_u16(sum, 8);
            }
        }
        vst4_u8(dst + i * 4, s);
    }
#elif defined(__SSE2__) || defined(_M_X64)
    const auto anti8 = _mm_set1_epi8(char(anti));
    const auto max_alpha = _mm_set1_epi32(255);
    for (; i + 4 <= pixels; i += 4) {
        auto s = _mm_loadu_si128(reinterpret_cast<const __m128i*>(src + i * 4));
        auto alpha = _mm_srli_epi32(s, 24);
        if constexpr (ExternalMask) {
            const auto m = _mm_loadu_si128(reinterpret_cast<const __m128i*>(mask + i * 4));
            alpha = _mm_andnot_si128(m, max_alpha);
            s = _mm_or_si128(_mm_and_si128(s, _mm_set1_epi32(0x00ffffff)), _mm_slli_epi32(alpha, 24));
        }
        auto opacity = _mm_subs_epu8(alpha, anti8);
        if (anti) opacity = _mm_andnot_si128(_mm_cmpeq_epi32(opacity, _mm_set1_epi32(1)), opacity);
        if (_mm_movemask_epi8(_mm_cmpeq_epi32(opacity, _mm_setzero_si128())) == 0xffff) continue;
        if (_mm_movemask_epi8(_mm_cmpeq_epi32(opacity, max_alpha)) != 0xffff) {
            const auto pairs = _mm_or_si128(opacity, _mm_slli_epi32(opacity, 16));
            const auto d = _mm_loadu_si128(reinterpret_cast<const __m128i*>(dst + i * 4));
            s = blend_block(d, s, _mm_unpacklo_epi32(pairs, pairs), _mm_unpackhi_epi32(pairs, pairs));
        }
        _mm_storeu_si128(reinterpret_cast<__m128i*>(dst + i * 4), s);
    }
#endif
    for (; i < pixels; ++i) {
        const auto* color = src + i * 4;
        auto* out = dst + i * 4;
        const unsigned alpha = ExternalMask ? 255u - mask[i * 4] : color[3];
        if (alpha <= anti + (anti != 0)) continue;
        const unsigned opacity = alpha - anti;
        for (int c = 0; c < 3; ++c) out[c] = channel(out[c], color[c], opacity);
        out[3] = channel(out[3], uint8_t(alpha), opacity);
    }
}

} // namespace majiro::raster
