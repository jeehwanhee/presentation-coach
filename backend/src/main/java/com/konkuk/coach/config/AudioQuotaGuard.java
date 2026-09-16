package com.konkuk.coach.config;

import io.github.bucket4j.Bandwidth;
import io.github.bucket4j.Bucket;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.concurrent.ConcurrentHashMap;

/**
 * submit 1회당 오디오는 10분(AUDIO_DURATION_EXCEEDED)까지 허용되지만, RateLimitFilter의
 * 하루 10회 제한을 다 채우면 이론상 하루 100분까지 분석 요청이 가능해서(=AI 비용도 그만큼)
 * IP당 하루 누적 오디오 총량을 별도로 제한한다. 2026-09-16 결정: 30분/일.
 * 인스턴스가 1대뿐이라 인메모리로 충분 — 여러 대로 늘리면 공유 저장소로 교체 필요.
 */
@Component
public class AudioQuotaGuard {

    private static final int DAILY_MINUTES_LIMIT = 30;

    private final ConcurrentHashMap<String, Bucket> buckets = new ConcurrentHashMap<>();

    public boolean tryConsume(String clientIp, long audioDurationMs) {
        int minutes = (int) ((audioDurationMs + 59_999) / 60_000);
        if (minutes <= 0) {
            minutes = 1;
        }
        Bucket bucket = buckets.computeIfAbsent(clientIp, k -> newDailyBucket());
        return bucket.tryConsumeAndReturnRemaining(minutes).isConsumed();
    }

    private Bucket newDailyBucket() {
        Bandwidth limit = Bandwidth.builder()
                .capacity(DAILY_MINUTES_LIMIT)
                .refillIntervally(DAILY_MINUTES_LIMIT, Duration.ofDays(1))
                .build();
        return Bucket.builder().addLimit(limit).build();
    }
}
