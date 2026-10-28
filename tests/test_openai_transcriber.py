import io
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile

from whisperer.config import WhispererConfig
from whisperer.transcription.openai_transcriber import OpenAITranscriber


class OpenAITranscriberTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.audio.transcriptions.create.return_value.text = "Transcript."
        self.transcriber = OpenAITranscriber(self.client, "gpt-4o-mini-transcribe", "")

    def test_silence_is_not_sent_to_api_or_pasted_as_invented_greeting(self):
        self.assertEqual(self.transcriber.transcribe(np.zeros(16000), 16000), "")
        self.client.audio.transcriptions.create.assert_not_called()
        self.assertEqual(WhispererConfig().transcription_prompt, "")

    def test_empty_prompt_is_omitted_and_flac_contains_the_recording(self):
        audio = np.full(16000, 0.1, dtype=np.float32)
        self.assertEqual(self.transcriber.transcribe(audio, 16000), "Transcript.")
        request = self.client.audio.transcriptions.create.call_args.kwargs
        self.assertNotIn("prompt", request)
        encoded = request["file"][1]
        decoded, rate = soundfile.read(io.BytesIO(encoded.getvalue()))
        self.assertEqual(rate, 16000)
        np.testing.assert_allclose(decoded, audio, atol=1 / 32768)

    def test_user_vocabulary_prompt_is_preserved(self):
        transcriber = OpenAITranscriber(self.client, "gpt-4o-mini-transcribe", "Whisperer, OpenAI")
        transcriber.transcribe(np.full(16000, 0.1), 16000)
        self.assertEqual(self.client.audio.transcriptions.create.call_args.kwargs["prompt"], "Whisperer, OpenAI")

    def long_audio(self):
        audio = np.full(73 * 1000, 0.1, dtype=np.float32)
        audio[29_800:30_200] = 0
        return audio

    def test_long_audio_splits_at_a_pause_without_losing_or_duplicating_samples(self):
        audio = self.long_audio()
        chunks = self.transcriber._split_at_pauses(audio, 1000)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(29_800 <= len(chunks[0]) <= 30_200)
        np.testing.assert_array_equal(np.concatenate(chunks), audio)

    def test_continuous_speech_without_a_quiet_boundary_is_not_cut_midword(self):
        audio = np.full(73_000, 0.1)
        chunks = self.transcriber._split_at_pauses(audio, 1000)
        self.assertEqual(len(chunks), 1)
        self.assertIs(chunks[0].base, audio)

    def test_parallel_chunks_are_joined_in_speech_order(self):
        second_finished = threading.Event()

        def transcribe_chunk(audio, rate):
            if len(audio) < 40_000:
                if not second_finished.wait(2):
                    raise RuntimeError("Second chunk did not run in parallel")
                return "First."
            second_finished.set()
            return "Second."

        with patch.object(self.transcriber, "_transcribe_chunk", side_effect=transcribe_chunk):
            self.assertEqual(self.transcriber.transcribe(self.long_audio(), 1000), "First. Second.")

    def test_failure_does_not_return_a_partial_transcript(self):
        def transcribe_chunk(audio, rate):
            if len(audio) < 40_000:
                return "First."
            raise RuntimeError("API unavailable")

        with patch.object(self.transcriber, "_transcribe_chunk", side_effect=transcribe_chunk):
            with self.assertRaisesRegex(RuntimeError, "API unavailable"):
                self.transcriber.transcribe(self.long_audio(), 1000)

    def test_chunking_can_be_disabled(self):
        transcriber = OpenAITranscriber(self.client, "gpt-4o-mini-transcribe", "", chunk_seconds=0)
        audio = self.long_audio()
        self.assertEqual(transcriber._split_at_pauses(audio, 1000), [audio])


if __name__ == "__main__":
    unittest.main()
