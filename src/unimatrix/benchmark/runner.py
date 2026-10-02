"""Barrier runner: persist successes before retrying infrastructure failures."""

import asyncio
import time

from jsonschema import ValidationError

from ..actions.resolver import resolve
from ..actions.schemas import validate
from ..core.ids import digest
from ..core.state import replica
from ..core.visibility import observe
from ..core.waiting import decision_packets
from .parallel import request_gate

RETRY_BACKOFF = (0.5, 2.0)


class InfrastructureFailure(RuntimeError):
    def __init__(self, *args, retryable=True):
        super().__init__(*args)
        self.retryable = retryable


class Runner:
    def __init__(self, store, scenario, router, concurrency=8):
        self.store, self.scenario, self.router = store, scenario, router

        self.semaphore = request_gate.get() or asyncio.Semaphore(concurrency)

    async def decision(self, state, slot, packet, generation_tokens=None):
        policy = self.router.policy(slot)
        request_id = digest([state.run_id, state.tick, slot, packet, policy.fingerprint])
        saved = self.store.decision(state.tick, slot, request_id)
        if saved is not None:
            return saved
        attempts = self.store.attempts(request_id)


        window = attempts // 3
        for attempt in range(attempts, (window + 1) * 3):
            local_attempt = attempt % 3
            if local_attempt:
                await asyncio.sleep(RETRY_BACKOFF[min(local_attempt, len(RETRY_BACKOFF)) - 1])
            started = time.monotonic()
            try:
                async with self.semaphore:
                    raw, usage = await policy.decide(
                        replica(packet),
                        dict(generation_tokens=generation_tokens, envelope_bytes=6144),
                    )
            except InfrastructureFailure as error:
                self.store.record_call(
                    request_id,
                    attempt,
                    dict(
                        slot=slot,
                        purpose="decision",
                        error=str(error),
                        retryable=error.retryable,
                        retry_window=window,
                        latency=time.monotonic() - started,
                    ),
                )


                if not error.retryable:
                    raise
                continue
            call = dict(
                usage, slot=slot, fingerprint=policy.fingerprint, retry_window=window,
                latency=time.monotonic() - started
            )
            self.store.save_decision(state.tick, slot, request_id, packet, raw, call=(attempt, call))
            return raw
        raise InfrastructureFailure("retry_budget_exhausted")

    async def run(self, until=None):
        self.store.verify()
        self.store.set_status("running")
        state = self.store.load()
        try:
            horizon = self.store.manifest["ticks"]
            limit = horizon if until is None else min(until, horizon)
            while state.tick < limit:
                if hasattr(self.router, "begin_tick"):
                    self.router.begin_tick(state)
                packets = {
                    slot: observe(state, slot, self.scenario)
                    for slot, a in sorted(state.agents.items())
                    if a["alive"]
                }
                active = decision_packets(state, packets)
                results = await asyncio.gather(
                    *(
                        self.decision(state, slot, packet)
                        for slot, packet in active.items()
                    ),
                    return_exceptions=True,
                )
                errors = [r for r in results if isinstance(r, BaseException)]
                if errors:
                    raise errors[0]
                decisions = {}
                for slot, raw in zip(active, results):
                    try:
                        decisions[slot] = validate(raw, state.tick, slot)
                    except (ValueError, TypeError, ValidationError):
                        decisions[slot] = None
                after, events = resolve(state, decisions, self.scenario, packets)
                state = self.store.commit(state, after, events)
                await asyncio.sleep(0)
        except InfrastructureFailure:
            self.store.set_status("infra_failed")
            raise
        except asyncio.CancelledError:
            self.store.set_status("paused")
            raise
        except Exception:
            self.store.set_status("engine_failed")
            raise
        self.store.set_status(
            "completed" if state.tick == self.store.manifest["ticks"] else "paused"
        )
        return state
