package com.konkuk.coach.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;

import java.io.PrintWriter;
import java.io.StringWriter;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RateLimitFilterTest {

    private final RateLimitFilter filter = new RateLimitFilter(new ObjectMapper());

    @Test
    @DisplayName("같은 IP로 하루 10회 초과 호출하면 11번째부터 429를 반환한다")
    void blocksAfterDailyLimitExceeded() throws Exception {
        for (int i = 0; i < 10; i++) {
            HttpServletRequest request = mockCreateRequest("1.2.3.4");
            HttpServletResponse response = mock(HttpServletResponse.class);
            FilterChain chain = mock(FilterChain.class);

            filter.doFilterInternal(request, response, chain);

            verify(chain, times(1)).doFilter(request, response);
            verify(response, never()).setStatus(429);
        }

        HttpServletRequest blockedRequest = mockCreateRequest("1.2.3.4");
        HttpServletResponse blockedResponse = mock(HttpServletResponse.class);
        StringWriter body = new StringWriter();
        when(blockedResponse.getWriter()).thenReturn(new PrintWriter(body));
        FilterChain blockedChain = mock(FilterChain.class);

        filter.doFilterInternal(blockedRequest, blockedResponse, blockedChain);

        verify(blockedChain, never()).doFilter(any(), any());
        verify(blockedResponse).setStatus(429);
        assertThat(body.toString()).contains("RATE_LIMIT_EXCEEDED");
    }

    @Test
    @DisplayName("다른 IP는 서로 영향을 주지 않는다")
    void differentIpsHaveSeparateBuckets() throws Exception {
        for (int i = 0; i < 10; i++) {
            filter.doFilterInternal(mockCreateRequest("9.9.9.9"), mock(HttpServletResponse.class), mock(FilterChain.class));
        }

        HttpServletRequest otherIpRequest = mockCreateRequest("8.8.8.8");
        HttpServletResponse otherIpResponse = mock(HttpServletResponse.class);
        FilterChain otherIpChain = mock(FilterChain.class);

        filter.doFilterInternal(otherIpRequest, otherIpResponse, otherIpChain);

        verify(otherIpChain, times(1)).doFilter(otherIpRequest, otherIpResponse);
        verify(otherIpResponse, never()).setStatus(429);
    }

    @Test
    @DisplayName("create/submit 이 아닌 요청은 제한 없이 통과한다")
    void passesThroughUnrelatedRequests() throws Exception {
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getMethod()).thenReturn("GET");
        when(request.getRequestURI()).thenReturn("/api/presentations/1");
        HttpServletResponse response = mock(HttpServletResponse.class);
        FilterChain chain = mock(FilterChain.class);

        filter.doFilterInternal(request, response, chain);

        verify(chain, times(1)).doFilter(request, response);
        verify(response, never()).setStatus(429);
    }

    @Test
    @DisplayName("submit 경로는 presentation_id와 무관하게 같은 IP면 같은 한도를 공유한다")
    void submitPathMatchesAnyPresentationId() throws Exception {
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getMethod()).thenReturn("POST");
        when(request.getRequestURI()).thenReturn("/api/presentations/123/submit");
        when(request.getHeader("X-Forwarded-For")).thenReturn("5.5.5.5");
        HttpServletResponse response = mock(HttpServletResponse.class);
        FilterChain chain = mock(FilterChain.class);

        filter.doFilterInternal(request, response, chain);

        verify(chain, times(1)).doFilter(request, response);
        verify(response, never()).setStatus(429);
    }

    private HttpServletRequest mockCreateRequest(String ip) {
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getMethod()).thenReturn("POST");
        when(request.getRequestURI()).thenReturn("/api/presentations");
        when(request.getHeader("X-Forwarded-For")).thenReturn(ip);
        return request;
    }
}
