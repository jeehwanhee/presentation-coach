package com.konkuk.coach.dto.response;

import com.konkuk.coach.exception.ErrorBody;

public record ErrorResponse (ErrorBody error) {

    public static ErrorResponse of(String code, String message) {
        return new ErrorResponse(new ErrorBody(code, message));
    }
}
