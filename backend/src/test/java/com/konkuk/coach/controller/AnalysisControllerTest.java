package com.konkuk.coach.controller;

import com.konkuk.coach.exception.BusinessException;
import com.konkuk.coach.exception.PresentationErrorCode;
import com.konkuk.coach.service.PresentationService;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(AnalysisController.class)
@TestPropertySource(properties = "app.worker.secret=test-secret")
class AnalysisControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private PresentationService presentationService;

    @Test
    @DisplayName("워커 시크릿이 일치하면 200을 반환한다")
    void receiveWithValidSecretReturns200() throws Exception {
        mockMvc.perform(post("/api/internal/analysis-results")
                        .header("X-Worker-Secret", "test-secret")
                        .contentType("application/json")
                        .content("""
                                {"presentation_id":1,"status":"DONE","transcript":{},"report":{}}
                                """))
                .andExpect(status().isOk());

        verify(presentationService).applyAnalysisResult(any());
    }

    @Test
    @DisplayName("워커 시크릿이 일치하지 않으면 403을 반환한다")
    void receiveWithInvalidSecretReturns403() throws Exception {
        mockMvc.perform(post("/api/internal/analysis-results")
                        .header("X-Worker-Secret", "wrong-secret")
                        .contentType("application/json")
                        .content("""
                                {"presentation_id":1,"status":"DONE"}
                                """))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("INVALID_WORKER_SECRET"));

        verify(presentationService, never()).applyAnalysisResult(any());
    }

    @Test
    @DisplayName("status가 DONE/FAILED가 아니면 400을 반환한다")
    void receiveWithInvalidStatusReturns400() throws Exception {
        mockMvc.perform(post("/api/internal/analysis-results")
                        .header("X-Worker-Secret", "test-secret")
                        .contentType("application/json")
                        .content("""
                                {"presentation_id":1,"status":"WRONG"}
                                """))
                .andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("존재하지 않는 presentationId면 404를 반환한다")
    void receiveWithUnknownPresentationReturns404() throws Exception {
        doThrow(new BusinessException(PresentationErrorCode.PRESENTATION_ID_NOT_FOUND))
                .when(presentationService).applyAnalysisResult(any());

        mockMvc.perform(post("/api/internal/analysis-results")
                        .header("X-Worker-Secret", "test-secret")
                        .contentType("application/json")
                        .content("""
                                {"presentation_id":999,"status":"DONE"}
                                """))
                .andExpect(status().isNotFound());
    }
}
