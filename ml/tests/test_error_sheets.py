"""error_sheets.find_errors: same matching rule as the metrics (greedy by confidence, IoU >= 0.5)."""
import error_sheets as es


def test_find_errors():
    preds = {
        "A": {"gt": [[3, 10, 10, 30, 30], [3, 50, 50, 70, 70]],
              "det": [[10, 10, 30, 30, .9, 3], [80, 80, 95, 95, .5, 3], [50, 50, 70, 70, .1, 3], [0, 0, 5, 5, .9, 0]]},
        "B": {"gt": [], "det": []},
    }
    misses, fas = es.find_errors(preds, 3, 0.2)
    assert [m["box"] for m in misses] == [[50, 50, 70, 70]]
    assert abs(misses[0]["best_conf"] - 0.1) < 1e-9  # the model saw it, but below the cut-off
    assert [f["box"] for f in fas] == [[80, 80, 95, 95]]
