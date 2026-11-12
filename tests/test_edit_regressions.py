"""Regression checks for frame boundaries, repair caching and active write locks."""
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import wave
import xml.etree.ElementTree as ET
from fractions import Fraction

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/cy-edit-longvideo/scripts'


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


common = module('lve_common')
packaging = module('cy_packaging')
quality = module('cy_quality')


class EditRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def owner(self, pid, age):
        directory = self.base / 'write-lock'
        directory.mkdir()
        value = json.dumps({'pid': pid, 'host': platform.node(),
                            'created': time.time() - age, 'token': 'existing'})
        (directory / common.LOCK_OWNER).write_text(value, encoding='utf-8')
        return directory, value

    def test_active_local_lock_survives_six_hour_limit(self):
        directory, original = self.owner(os.getpid(), common.LOCK_STALE_SECONDS + 60)
        self.assertTrue(common._pid_alive(os.getpid()))
        with self.assertRaises(common.UserError):
            with common.lock(directory):
                self.fail('Live writer lock was stolen')
        self.assertEqual((directory / common.LOCK_OWNER).read_text(encoding='utf-8'), original)

    def test_dead_local_owner_is_recovered_without_waiting(self):
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait(timeout=10)
        directory, _ = self.owner(child.pid, 0)
        self.assertFalse(common._pid_alive(child.pid))
        with common.lock(directory):
            value = json.loads((directory / common.LOCK_OWNER).read_text(encoding='utf-8'))
            self.assertEqual(value['pid'], os.getpid())
        self.assertFalse(directory.exists())

    def test_legacy_lock_only_recovers_after_timeout(self):
        directory = self.base / 'legacy'
        directory.mkdir()
        with self.assertRaises(common.UserError):
            with common.lock(directory):
                self.fail('Fresh legacy lock was stolen')
        old = time.time() - common.LOCK_STALE_SECONDS - 60
        os.utime(directory, (old, old))
        with common.lock(directory):
            self.assertTrue((directory / common.LOCK_OWNER).is_file())
        self.assertFalse(directory.exists())

    def test_adjacent_captions_do_not_overlap_after_frame_rounding(self):
        xml = ET.Element('fcpxml', version='1.10')
        ET.SubElement(xml, 'resources')
        clip = ET.SubElement(xml, 'asset-clip')
        captions = [{'start': 0, 'end': 2.01, 'text': '第一句'},
                    {'start': 2.01, 'end': 4.03, 'text': '第二句'},
                    {'start': 4.03, 'end': 8, 'text': '第三句'}]
        copy = {'content_title': ['内容标题', '第二行'], 'cover_title': ['封面参考', '另一行']}
        packaging.decorate(xml, [{'node': clip, 'start': Fraction(0), 'offset': Fraction(0),
                                  'duration': Fraction(10), 'id': 'clip'}],
                           captions, copy, self.base, 1920, 1080, Fraction(1, 25))
        titles = [t for t in clip.findall('title') if t.get('name') == '字幕']
        self.assertEqual(len(titles), 3)
        end = Fraction(0)
        for title in titles:
            start = Fraction(title.get('offset')[:-1])
            duration = Fraction(title.get('duration')[:-1])
            self.assertGreaterEqual(start, end)
            self.assertEqual((start * 25).denominator, 1)
            self.assertEqual((duration * 25).denominator, 1)
            end = start + duration

    def test_layout_adapts_to_canvas_without_changing_relative_text_scale(self):
        for width, height, anchor in ((1080, 1920, 'center'), (1080, 1440, 'center'),
                                       (1080, 1080, 'top-center'), (1440, 1080, 'top-center'),
                                       (1920, 1080, 'top-left'), (3840, 2160, 'top-left')):
            with self.subTest(canvas=(width, height)):
                layout = packaging.layout_for(width, height)
                self.assertEqual(layout['title_anchor'], anchor)
                x, y = packaging.title_position(layout, ['内容标题', '第二行'], width, height)
                self.assertLess(abs(x), width / height * 50)
                self.assertLess(abs(y), 50)
                self.assertGreater(layout['sub_chars'], 0)
        hd = packaging.layout_for(1920, 1080)
        uhd = packaging.layout_for(3840, 2160)
        self.assertEqual(hd['sub_size'], uhd['sub_size'])
        self.assertEqual(hd['sub_chars'], uhd['sub_chars'])
        self.assertLess(packaging.text_px('hello', 52), packaging.text_px('你好世界呀', 52))

    def test_recheck_cache_reuses_audio_and_invalidates_changed_model_or_audio(self):
        audio = self.base / 'audio.wav'
        def write_audio(sample):
            with wave.open(str(audio), 'wb') as output:
                output.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                output.writeframes(sample * 8000)
        write_audio(b'\x00\x00')
        recognize = Mock(return_value={'segments': [{'words': [
            {'start': 0.1, 'end': 0.6, 'word': '测试'}]}]})
        with patch.dict(os.environ, {'CY_EDIT_RECHECK_CACHE': str(self.base / 'cache')}):
            first = quality.retranscribe(audio, self.base / 'first', recognize, key='model-a:zh')
            again = quality.retranscribe(audio, self.base / 'again', recognize, key='model-a:zh')
            self.assertEqual(first, again)
            self.assertEqual(recognize.call_count, 1)
            quality.retranscribe(audio, self.base / 'model', recognize, key='model-b:zh')
            self.assertEqual(recognize.call_count, 2)
            write_audio(b'\x01\x00')
            quality.retranscribe(audio, self.base / 'audio', recognize, key='model-b:zh')
            self.assertEqual(recognize.call_count, 3)

    def test_shared_implementation_matches_both_packages(self):
        for name in ('lve_common.py', 'cy_packaging.py', 'cy_quality.py'):
            self.assertEqual((SCRIPTS / name).read_bytes(),
                             (ROOT / 'skills/cy-edit-shortvideo/scripts' / name).read_bytes())


if __name__ == '__main__':
    unittest.main()
