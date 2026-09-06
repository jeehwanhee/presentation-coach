package com.konkuk.coach.exception;

import org.springframework.http.HttpStatus;

public enum PresentationErrorCode implements CustomErrorCode {
    PRESENTATION_EXPIRED(HttpStatus.GONE, "PRESENTATION_EXPIRED", "리포트가 만료되었습니다."),
    PRESENTATION_NOT_FOUND(HttpStatus.FORBIDDEN, "PRESENTATION_NOT_FOUND", "존재하지 않거나 접근 권한이 없는 발표입니다."),
    INVALID_WORKER_SECRET(HttpStatus.FORBIDDEN, "INVALID_WORKER_SECRET", "워커 시크릿이 일치하지 않습니다."),
    AUDIO_DURATION_EXCEEDED(HttpStatus.BAD_REQUEST, "AUDIO_DURATION_EXCEEDED", "오디오 길이가 10분 제한을 초과했습니다.");

    private final HttpStatus httpStatus;
    private final String code;
    private final String message;

    PresentationErrorCode(HttpStatus httpStatus, String code, String message) {
        this.httpStatus = httpStatus;
        this.code = code;
        this.message = message;
    }

    @Override
    public HttpStatus getHttpStatus() {
        return httpStatus;
    }

    @Override
    public String getCode() {
        return code;
    }

    @Override
    public String getMessage() {
        return message;
    }
}
