package com.konkuk.coach.domain;

public enum PresentationStatus {
    PENDING,      // 제출됨, 큐 대기
    PROCESSING,   // 워커가 처리 중
    DONE,         // 분석 완료
    FAILED        // 실패
}