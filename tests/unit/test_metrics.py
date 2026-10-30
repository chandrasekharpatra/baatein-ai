from baatein.metrics import CallMetrics, percentile, report


def test_percentile_interpolates_and_handles_no_data() -> None:
    assert percentile([], 50) is None
    assert percentile([100.0], 90) == 100.0
    assert percentile([100.0, 200.0, 300.0, 400.0], 50) == 250.0
    assert percentile([400.0, 100.0, 300.0, 200.0], 90) == 370.0


def test_metrics_round_trip_and_ignore_unknown_fields() -> None:
    metrics = CallMetrics(reply_ms=[900.0], searches=2)

    assert CallMetrics.from_json(metrics.to_json() | {"from_a_newer_version": 1}) == metrics


def test_report_sums_calls() -> None:
    calls = [
        CallMetrics(
            reply_ms=[800.0, 1200.0],
            user_turns=3,
            replies=2,
            interrupted=1,
            searches=2,
            searches_skipped=1,
            search_ms=[450.0, 500.0],
            memories_now=1,
            memories_late=3,
            recall_calls=1,
            thinking_said=1,
            saved=2,
            already_saved=1,
            llm_input_tokens=5000,
            llm_cached_tokens=4000,
            llm_output_tokens=120,
            stt_audio_seconds=90.0,
            tts_characters=400,
        ),
        CallMetrics(reply_ms=[1000.0], user_turns=1, replies=1),
    ]

    text = report(calls)

    assert "Calls: 2, caller turns: 4, replies: 3" in text
    assert "reply            p50 1000 ms, p90 1160 ms (3)" in text
    assert "transcript       no data" in text
    assert "interrupted      1/3 (33%) replies" in text
    assert "turns searched   2/4 (50%) (skipped as fillers: 1)" in text
    assert "memories late    3/4 (75%)" in text
    assert 'recall tool      1 calls, "Hmm…" said 1' in text
    assert "saved            2, already saved 1" in text
    assert "Claude tokens    5000 in (4000 cached), 120 out" in text
    assert "speech-to-text   1.5 min" in text


def test_report_without_calls() -> None:
    assert report([]) == "No calls with metrics yet."
    assert "turns searched   0/0" in report([CallMetrics()])
