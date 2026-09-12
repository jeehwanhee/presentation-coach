package com.konkuk.coach.config;

import com.konkuk.coach.dto.response.ErrorResponse;
import io.github.bucket4j.Bandwidth;
import io.github.bucket4j.Bucket;
import io.github.bucket4j.ConsumptionProbe;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.time.Duration;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;

/**
 * create/submit는 호출당 AI(STT·LLM) 비용이 들거나 리소스를 소비하므로,
 * 악의적 사용자가 API를 직접 두드려 무제한 호출하는 것을 막기 위한 IP 기준 일일 제한.
 * 인스턴스가 1대뿐이라 인메모리로 충분 — 여러 대로 늘리면 공유 저장소(Redis 등)로 교체 필요.
 */
@Component
public class RateLimitFilter extends OncePerRequestFilter {

    private static final int DAILY_LIMIT = 10;
    private static final Pattern SUBMIT_PATH = Pattern.compile("^/api/presentations/\\d+/submit$");

    private final ObjectMapper objectMapper;
    private final ConcurrentHashMap<String, Bucket> buckets = new ConcurrentHashMap<>();

    public RateLimitFilter(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {

        String action = resolveLimitedAction(request);
        if (action == null) {
            chain.doFilter(request, response);
            return;
        }

        String key = resolveClientIp(request) + ":" + action;
        Bucket bucket = buckets.computeIfAbsent(key, k -> newDailyBucket());

        ConsumptionProbe probe = bucket.tryConsumeAndReturnRemaining(1);
        if (!probe.isConsumed()) {
            response.setStatus(429);
            response.setContentType("application/json;charset=UTF-8");
            response.getWriter().write(objectMapper.writeValueAsString(
                    ErrorResponse.of("RATE_LIMIT_EXCEEDED", "하루 이용 횟수(10회)를 초과했습니다. 내일 다시 시도해주세요.")
            ));
            return;
        }

        chain.doFilter(request, response);
    }

    private String resolveLimitedAction(HttpServletRequest request) {
        if (!"POST".equals(request.getMethod())) {
            return null;
        }
        if ("/api/presentations".equals(request.getRequestURI())) {
            return "create";
        }
        if (SUBMIT_PATH.matcher(request.getRequestURI()).matches()) {
            return "submit";
        }
        return null;
    }

    private Bucket newDailyBucket() {
        Bandwidth limit = Bandwidth.builder()
                .capacity(DAILY_LIMIT)
                .refillIntervally(DAILY_LIMIT, Duration.ofDays(1))
                .build();
        return Bucket.builder().addLimit(limit).build();
    }

    private String resolveClientIp(HttpServletRequest request) {
        String forwardedFor = request.getHeader("X-Forwarded-For");
        if (forwardedFor != null && !forwardedFor.isBlank()) {
            return forwardedFor.split(",")[0].trim();
        }
        return request.getRemoteAddr();
    }
}
