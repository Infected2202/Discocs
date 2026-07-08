from app.api.middleware import should_log_http_request


def test_should_log_http_request_matches_expected_paths():
    assert should_log_http_request("/stats") is True
    assert should_log_http_request("/jobs") is True
    assert should_log_http_request("/metrics") is True
    assert should_log_http_request("/metrics/prometheus") is True
    assert should_log_http_request("/navidrome/similar") is True
    assert should_log_http_request("/instant-mix") is True
    assert should_log_http_request("/text-search/releases") is True
    assert should_log_http_request("/tracks/42/cover") is True
    assert should_log_http_request("/tracks/42/similar") is True
    assert should_log_http_request("/tracks/42/navidrome-star") is True

    assert should_log_http_request("/tracks/42") is False
    assert should_log_http_request("/artists/1") is False
    assert should_log_http_request("/playback/queue") is False
