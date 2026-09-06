package com.konkuk.coach.exception;

import org.springframework.http.HttpStatus;

public interface CustomErrorCode {
    HttpStatus getHttpStatus();
    String getCode();
    String getMessage();
}
