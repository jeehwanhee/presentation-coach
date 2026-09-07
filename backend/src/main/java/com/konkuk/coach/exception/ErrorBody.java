package com.konkuk.coach.exception;

public record ErrorBody(
        String code,
        String message
) {
}
