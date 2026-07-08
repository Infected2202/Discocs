from app.api.middleware import should_log_http_request


def test_should_log_http_request_matches_expected_paths():
    assert should_log_http_request("/api/v1/stats") is True
    assert should_log_http_request("/api/v1/jobs") is True
    assert should_log_http_request("/api/v1/metrics") is True
    assert should_log_http_request("/api/v1/metrics/prometheus") is True
    assert should_log_http_request("/api/v1/navidrome/similar") is True
    assert should_log_http_request("/api/v1/instant-mix") is True
    assert should_log_http_request("/api/v1/text-search/releases") is True
    assert should_log_http_request("/api/v1/tracks/42/cover") is True
    assert should_log_http_request("/api/v1/tracks/42/similar") is True
    assert should_log_http_request("/api/v1/tracks/42/navidrome-star") is True

    assert should_log_http_request("/api/v1/tracks/42") is False
    assert should_log_http_request("/api/v1/artists/1") is False
    assert should_log_http_request("/api/v1/playback/queue") is False
