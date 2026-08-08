from classifier.evaluate import binary_metrics, select_thresholds


def test_metrics_report_both_classes_and_confusion_counts():
    metrics = binary_metrics([1, 1, 0, 0], [0.9, 0.4, 0.6, 0.1])

    assert metrics["accuracy"] == 0.5
    assert metrics["confusion_matrix"] == {"tn": 1, "fp": 1, "fn": 1, "tp": 1}
    assert metrics["join"] == {"precision": 0.5, "recall": 0.5}
    assert metrics["keep_spaced"] == {"precision": 0.5, "recall": 0.5}


def test_thresholds_maximize_recall_at_required_precision():
    thresholds = select_thresholds(
        [1, 1, 0, 0],
        [0.99, 0.80, 0.20, 0.01],
        minimum_join_precision=0.97,
        minimum_keep_precision=0.97,
    )

    assert thresholds == {"join_threshold": 0.8, "keep_spaced_threshold": 0.2}

