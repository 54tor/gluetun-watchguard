from gluetun_watchguard.debounce import FailureTracker


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make(threshold=3, cooldown=300, grace=60):
    clock = Clock()
    return FailureTracker(threshold, cooldown, grace, clock=clock), clock


def test_no_action_before_threshold():
    tracker, clock = make()
    clock.advance(120)  # past grace
    tracker.record_failure()
    tracker.record_failure()
    assert not tracker.should_act()


def test_action_after_threshold_past_grace():
    tracker, clock = make()
    clock.advance(120)
    for _ in range(3):
        tracker.record_failure()
    assert tracker.should_act()


def test_grace_blocks_early_action():
    tracker, clock = make(grace=60)
    for _ in range(5):
        tracker.record_failure()
    assert not tracker.should_act()  # still within startup grace
    clock.advance(61)
    assert tracker.should_act()


def test_success_resets_counter():
    tracker, clock = make()
    clock.advance(120)
    tracker.record_failure()
    tracker.record_failure()
    tracker.record_success()
    tracker.record_failure()
    assert not tracker.should_act()


def test_cooldown_and_regrace_block_repeat_action():
    tracker, clock = make(cooldown=300, grace=60)
    clock.advance(120)
    for _ in range(3):
        tracker.record_failure()
    assert tracker.should_act()
    tracker.mark_action()

    # A fresh burst of failures right after acting must not act again.
    for _ in range(3):
        tracker.record_failure()
    assert not tracker.should_act()

    # Once cooldown (and the renewed grace) elapse, acting is allowed again.
    clock.advance(301)
    for _ in range(3):
        tracker.record_failure()
    assert tracker.should_act()
