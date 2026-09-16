package com.konkuk.coach.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class WebConfig implements WebMvcConfigurer {

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/**")
                .allowedOriginPatterns("*")
                .allowedMethods("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
                .allowedHeaders("*");
    }

    /**
     * 심사용 예시 음성 파일(잘한 예시/못한 예시) 서빙 — git엔 안 올리고 서버 파일시스템에서만
     * 관리(용량·개인 녹음이라 저장소에 안 올리기로 결정, 2026-09-16). /api/** 는 CloudFront가
     * 항상 이 백엔드로 라우팅하는 걸 이미 확인한 경로라 그 아래에 둠.
     */
    @Override
    public void addResourceHandlers(ResourceHandlerRegistry registry) {
        registry.addResourceHandler("/api/examples/**")
                .addResourceLocations("file:/home/admin/ptpt-example-media/");
    }
}
