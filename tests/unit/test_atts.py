"""
Unit tests for the Adaptive Turn-Taking Scheduler (ATTS).
Validates state machine transitions, VAD logic, and barge-in handling.
"""
import asyncio
import pytest
from omni_voice.core.atts import AdaptiveTurnTakingScheduler, TurnState


@pytest.mark.asyncio
class TestATTSStateMachine:
    async def test_starts_idle(self):
        atts = AdaptiveTurnTakingScheduler()
        assert atts.state == TurnState.idle

    async def test_speech_start_transitions_to_listening(self):
        atts = AdaptiveTurnTakingScheduler()
        await atts.on_speech_start()
        assert atts.state == TurnState.listening

    async def test_silence_after_margin_transitions_to_thinking(self):
        # Use a very small margin (1 epoch = 10ms) so the test is fast
        atts = AdaptiveTurnTakingScheduler(epoch_ms=10, margin_ms=10)
        await atts.on_speech_start()
        assert atts.state == TurnState.listening
        # Wait for margin
        await asyncio.sleep(0.05)
        state = await atts.on_vad_silence()
        assert state == TurnState.thinking

    async def test_first_token_transitions_to_speaking(self):
        atts = AdaptiveTurnTakingScheduler(epoch_ms=10, margin_ms=10)
        await atts.on_speech_start()
        await asyncio.sleep(0.05)
        await atts.on_vad_silence()
        await atts.on_first_token()
        assert atts.state == TurnState.speaking

    async def test_turn_complete_returns_to_idle(self):
        atts = AdaptiveTurnTakingScheduler(epoch_ms=10, margin_ms=10)
        await atts.on_speech_start()
        await asyncio.sleep(0.05)
        await atts.on_vad_silence()
        await atts.on_first_token()
        await atts.on_turn_complete()
        assert atts.state == TurnState.idle

    async def test_reset_returns_to_idle(self):
        atts = AdaptiveTurnTakingScheduler()
        await atts.on_speech_start()
        atts.reset()
        assert atts.state == TurnState.idle


@pytest.mark.asyncio
class TestATTSBargeIn:
    async def test_barge_in_while_speaking_transitions_to_interrupt(self):
        atts = AdaptiveTurnTakingScheduler(epoch_ms=10, margin_ms=10)
        await atts.on_speech_start()
        await asyncio.sleep(0.05)
        await atts.on_vad_silence()
        await atts.on_first_token()
        assert atts.state == TurnState.speaking

        interrupted = await atts.handle_barge_in(audio_energy=0.5, threshold=0.01)
        assert interrupted is True
        assert atts.state == TurnState.interrupt

    async def test_barge_in_when_idle_does_nothing(self):
        atts = AdaptiveTurnTakingScheduler()
        interrupted = await atts.handle_barge_in(audio_energy=0.5)
        assert interrupted is False
        assert atts.state == TurnState.idle

    async def test_low_energy_barge_in_ignored(self):
        atts = AdaptiveTurnTakingScheduler(epoch_ms=10, margin_ms=10)
        await atts.on_speech_start()
        await asyncio.sleep(0.05)
        await atts.on_vad_silence()
        await atts.on_first_token()

        interrupted = await atts.handle_barge_in(audio_energy=0.001, threshold=0.1)
        assert interrupted is False
        assert atts.state == TurnState.speaking


@pytest.mark.asyncio
class TestATTSTokenGating:
    async def test_gate_token_initially_succeeds(self):
        atts = AdaptiveTurnTakingScheduler(burst_capacity=5)
        allowed = await atts.gate_token()
        assert allowed is True

    async def test_state_change_callback_invoked(self):
        transitions = []
        atts = AdaptiveTurnTakingScheduler(
            epoch_ms=10,
            margin_ms=10,
            on_state_change=lambda old, new: transitions.append((old, new)),
        )
        await atts.on_speech_start()
        assert len(transitions) == 1
        assert transitions[0] == (TurnState.idle, TurnState.listening)
