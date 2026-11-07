from backend.inventory import load_inventory


class FakeClock:
    def __init__(self, t: float = 1_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def fresh_inventory(clock: FakeClock | None = None):
    """A new inventory loaded from the sample data, so tests never share stock state."""
    return load_inventory(clock=clock or FakeClock())


def codes(result) -> list[str]:
    return [r.code for r in result.reasons]
