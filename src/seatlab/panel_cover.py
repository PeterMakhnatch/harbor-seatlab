"""Exact minimum witness cover for observed panel selection (pure stdlib)."""

from __future__ import annotations

from collections.abc import Mapping


def minimum_panel(case_witnesses: Mapping[str, frozenset[str]]) -> tuple[str, ...]:
    """Return the fewest case labels whose witness sets cover every target.

    Targets are the union of all witness sets. Among minimum-size covers the
    lexicographically smallest sorted label tuple wins. No witnesses -> ``()``.

    Dynamic program over coverage bit masks. Cases are visited in sorted label
    order, so each stored tuple is already sorted. For one mask the state keeps
    the smallest ``(count, tuple)``: equal counts mean equal tuple lengths, so a
    lexicographically smaller prefix stays smaller after any common suffix.
    Cases with identical masks are deduplicated, keeping the earliest label
    (swapping in a smaller label never makes a sorted tuple larger).
    Work is at most (distinct masks <= 256) x (reachable masks <= 2**16).
    """
    targets = sorted(set().union(*case_witnesses.values())) if case_witnesses else []
    if not targets:
        return ()
    bit = {t: 1 << i for i, t in enumerate(targets)}
    full = (1 << len(targets)) - 1
    cases: list[tuple[str, int]] = []
    seen: set[int] = set()
    for label in sorted(case_witnesses):
        mask = 0
        for t in case_witnesses[label]:
            mask |= bit[t]
        if mask and mask not in seen:
            seen.add(mask)
            cases.append((label, mask))

    states: dict[int, tuple[int, tuple[str, ...]]] = {0: (0, ())}
    best = len(targets) + 1  # size of the best full cover found so far
    for label, cmask in cases:
        for mask, (count, picked) in list(states.items()):
            new = mask | cmask
            if new == mask:
                continue
            count += 1
            if count > best or (count == best and new != full):
                continue
            cand = (count, (*picked, label))
            old = states.get(new)
            if old is None or cand < old:
                states[new] = cand
                if new == full:
                    best = count
    return states[full][1]
