package com.konkuk.coach.exception;

public class BusinessException extends RuntimeException {

    private final CustomErrorCode errorCode;

    public BusinessException(CustomErrorCode errorCode) {
        super(errorCode.getMessage());
        this.errorCode = errorCode;
    }

    public BusinessException(CustomErrorCode errorCode, String message) {
        super(message);
        this.errorCode = errorCode;
    }

    public CustomErrorCode getErrorCode() {
        return errorCode;
    }
}
