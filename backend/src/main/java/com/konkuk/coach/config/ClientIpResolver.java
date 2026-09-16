package com.konkuk.coach.config;

import jakarta.servlet.http.HttpServletRequest;

/**
 * CloudFront 뒤에서는 request.getRemoteAddr()가 CloudFront 엣지 IP만 주므로,
 * X-Forwarded-For(CloudFront가 기본으로 붙여줌)의 첫 번째 값을 실제 클라이언트 IP로 쓴다.
 * RateLimitFilter/AudioQuotaGuard가 공통으로 사용.
 */
public final class ClientIpResolver {

    private ClientIpResolver() {
    }

    public static String resolve(HttpServletRequest request) {
        String forwardedFor = request.getHeader("X-Forwarded-For");
        if (forwardedFor != null && !forwardedFor.isBlank()) {
            return forwardedFor.split(",")[0].trim();
        }
        return request.getRemoteAddr();
    }
}
