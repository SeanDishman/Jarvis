import io
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import wave

from desktop.breaks import BreakState
from desktop.controller import FIRST_SPEECH_CHUNK, SPEECH_CHUNK, Controller, speech_chunks

REPLIES = [
    "It's 72 degrees and sunny in Chicago right now, sir, with a light breeze from the west. Later this afternoon clouds "
    "roll in, and there is a 40 percent chance of rain around 6 pm, so an umbrella would be wise if you're heading out "
    "this evening. Tomorrow looks clearer, with a high of 68 and plenty of sunshine through the day.",
    'Good morning, sir. A Thursday is a fine day to make a start, as William James wrote: “Act as if what you do makes a '
    'difference. It does.” Let’s put that difference to work.',
    'The version is 3.5.1 and it was released on Nov. 3 by Mr. Smith, who said it was fine. Dr. Jones agreed.',
    'Right away, sir.',
    'x' * 500,
    'word ' * 120,
    '  Leading and trailing spaces stay put.  ',
]


class SpeechChunkTests(unittest.TestCase):
    def test_pieces_always_join_back_to_exactly_what_was_written(self):
        for text in REPLIES + ['', 'One.', 'No punctuation at all here']:
            with self.subTest(text=text[:30]):
                self.assertEqual(''.join(speech_chunks(text)), text)

    def test_the_opening_is_short_so_speech_starts_quickly(self):
        for text in REPLIES:
            with self.subTest(text=text[:30]):
                self.assertLessEqual(len(speech_chunks(text)[0]), FIRST_SPEECH_CHUNK)
        self.assertEqual(speech_chunks('Right away, sir.'), ['Right away, sir.'])
        # Before, a long reply waited for 220 characters of audio before any sound.
        self.assertEqual(speech_chunks(REPLIES[0])[0], "It's 72 degrees and sunny in Chicago right now, sir, with a light breeze from the west. ")

    def test_each_later_piece_is_at_most_twice_the_one_before_so_playback_never_waits(self):
        for text in REPLIES:
            chunks = speech_chunks(text)
            for before, after in zip(chunks, chunks[1:]):
                with self.subTest(text=text[:30], after=after[:20]):
                    self.assertLessEqual(len(after), min(SPEECH_CHUNK, max(FIRST_SPEECH_CHUNK, 2 * len(before))))

    def test_pieces_end_at_sentences_or_clauses_not_in_the_middle_of_a_phrase(self):
        for chunk in speech_chunks(REPLIES[0]) + speech_chunks(REPLIES[1])[:-1]:
            self.assertRegex(chunk.rstrip(), r'[.!?:,;”]$', chunk)
        self.assertEqual(speech_chunks(REPLIES[2])[0].strip(),
                         'The version is 3.5.1 and it was released on Nov. 3 by Mr. Smith, who said it was fine.')


class CachedSpeechTests(unittest.TestCase):
    def test_a_line_synthesized_ahead_of_time_plays_from_the_cache_in_one_piece(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with patch('desktop.controller.BreakState', return_value=BreakState(Path(folder.name) / 'break.json')):
            controller = Controller(queue.Queue())
        for service in (controller.research, controller.tasks, controller.browser):
            self.addCleanup(service.close)
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\1\0' * 800)
        line = 'X is closed, sir. Back to the work. That is twice now.'
        controller.speech_cache[line] = buffer.getvalue()
        stream = MagicMock()
        stream.__enter__.return_value = stream
        with patch('desktop.controller.diagnostic'), patch('phone.voice.synthesize_wav') as synthesize, \
                patch('sounddevice.RawOutputStream', return_value=stream):
            controller.speak(line)
        synthesize.assert_not_called()
        self.assertEqual(b''.join(call.args[0] for call in stream.write.call_args_list), b'\1\0' * 800)


if __name__ == '__main__':
    unittest.main()
