"""Title choices in both editing workflows and compatibility with old plans."""
from fractions import Fraction
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


SKILLS = Path(__file__).resolve().parents[1] / 'skills'
TITLES = {
    'content_title': [
        ['先理清主线', '再删除重复'],
        ['口播保留推进信息', '剪掉同义复述'],
        ['两句意思一样', '保留表达更完整的一句'],
    ],
    'cover_title': [
        ['剪短一点', '就讲清楚了吗'],
        ['同一句来回讲', '该留下哪一次'],
        ['口播想讲明白', '哪种重复可以删'],
    ],
}
LEGACY_TITLES = {key: choices[0] for key, choices in TITLES.items()}


def scripts(skill):
    return SKILLS / f'cy-edit-{skill}video' / 'scripts'


def load_packaging(skill):
    spec = importlib.util.spec_from_file_location(f'{skill}_packaging', scripts(skill) / 'cy_packaging.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return path


class TitleAssertions(unittest.TestCase):
    def assert_titles(self, sequence, cover_duration):
        for key, name, duration in (
            ('content_title', '内容标题', sequence.get('duration')),
            ('cover_title', '封面标题参考', cover_duration),
        ):
            with self.subTest(group=key):
                names = [name, f'{name}（备选 2）', f'{name}（备选 3）']
                group = [title for title in sequence.findall('.//title') if title.get('name') in names]
                self.assertEqual([title.get('name') for title in group], names)
                self.assertEqual([title.get('enabled', '1') for title in group], ['1', '0', '0'])
                self.assertEqual([title.get('duration') for title in group], [duration] * 3)
                self.assertEqual(len({title.get('lane') for title in group}), 3)
                for title, lines in zip(group, TITLES[key]):
                    text = title.find('text/text-style')
                    self.assertEqual(text.text, '\n'.join(lines))
                    self.assertEqual(text.get('ref'), title.find('text-style-def').get('id'))
        self.assertEqual(len(sequence.findall('.//title[@enabled="0"]')), 4)


class TitleOptions(TitleAssertions):
    def test_accepts_legacy_and_preserves_single_titles(self):
        for skill in ('long', 'short'):
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as tmp:
                packaging = load_packaging(skill)
                root = ET.Element('fcpxml', version='1.10')
                ET.SubElement(root, 'resources')
                clip = ET.SubElement(root, 'asset-clip')
                hosts = [{'node': clip, 'id': 's001', 'offset': Fraction(0),
                          'start': Fraction(0), 'duration': Fraction(3)}]
                notes = packaging.decorate(root, hosts, [], LEGACY_TITLES, tmp, 1080, 1920, Fraction(1, 25))
                titles = [title for title in clip.findall('title') if title.get('name') in (
                    '内容标题', '封面标题参考',
                )]
                self.assertEqual(len(titles), 2)
                self.assertEqual([title.findtext('text/text-style') for title in titles],
                                 ['\n'.join(lines) for lines in LEGACY_TITLES.values()])
                self.assertEqual([title.get('duration') for title in titles], ['3s', '3s'])
                self.assertFalse(clip.findall('title[@enabled="0"]'))
                self.assertTrue(any('旧计划' in note for note in notes))

    def test_rejects_incomplete_empty_duplicate_and_multiline_choices(self):
        choices = TITLES['content_title']
        invalid = [
            choices[:2],
            choices[:1] + [['', ' ']] + choices[2:],
            choices[:1] + [['先理清主线再', '删除重复']] + choices[2:],
            choices[:1] + [['两行\n写在一起', '第二行']] + choices[2:],
            choices[:1] + [TITLES['cover_title'][0]] + choices[2:],
            choices[0],
        ]
        for skill in ('long', 'short'):
            packaging = load_packaging(skill)
            for case in invalid:
                with self.subTest(skill=skill, case=case), self.assertRaises(ValueError):
                    packaging.title_options(dict(TITLES, content_title=case))

    def test_all_title_choices_remain_editable_and_frame_aligned(self):
        for skill in ('long', 'short'):
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as tmp:
                packaging = load_packaging(skill)
                root = ET.Element('fcpxml', version='1.10')
                ET.SubElement(root, 'resources')
                seq = ET.SubElement(root, 'sequence', duration='3s')
                clip = ET.SubElement(seq, 'asset-clip')
                hosts = [{'node': clip, 'id': 's001', 'offset': Fraction(0),
                          'start': Fraction(2), 'duration': Fraction(3)}]
                packaging.decorate(root, hosts, [], TITLES, tmp, 1920, 1080, Fraction(1, 25))
                self.assert_titles(seq, '3s')
                for title in seq.findall('.//title'):
                    self.assertEqual(title.get('offset'), '2s')


@unittest.skipUnless(shutil.which('ffmpeg') and importlib.util.find_spec('av'), '需要 FFmpeg 和 PyAV')
class TitleWorkflow(TitleAssertions):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.output = self.base / 'delivery'
        self.source = self.base / 'source.mp4'
        self.srt = self.base / 'source.srt'
        self.srt.write_text(
            '1\n00:00:01,000 --> 00:00:19,900\n先理清主线\n\n'
            '2\n00:00:20,000 --> 00:02:04,000\n再删掉重复表达\n',
            encoding='utf-8',
        )
        self.env = dict(os.environ, CY_EDIT_AUTO_UPDATE='0',
                        CY_EDIT_EXPERIENCE_DIR=str(self.base / 'experience'))

    def media_fixture(self):
        subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error',
            '-f', 'lavfi', '-i', 'color=s=320x180:r=25:d=125',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=125',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', str(self.source),
        ], check=True, timeout=30)

    def editor(self, skill, *args, expected_code=0):
        result = subprocess.run(
            [sys.executable, str(scripts(skill) / 'editor.py'), *map(str, args)],
            env=self.env, capture_output=True, text=True, encoding='utf-8', timeout=60,
        )
        self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def assert_delivery(self, folder, projects):
        [path] = folder.glob('*.fcpxml')
        root = ET.parse(path)
        sequences = root.findall('.//sequence')
        self.assertEqual(len(sequences), projects)
        for seq in sequences:
            self.assert_titles(seq, '5s')
            # Both chapter starts are inside a single retained clip. The second
            # boundary is 19 seconds after the first and halfway across the bar.
            [fill] = [node for node in seq.iter('video') if node.get('name') == '进度条填充']
            keys = fill.findall('adjust-transform/param/keyframeAnimation/keyframe')
            self.assertEqual(len(keys), 3)
            self.assertEqual(Fraction(keys[1].get('time').removesuffix('s')), 19)
            self.assertAlmostEqual(float(keys[1].get('value').split()[0]),
                                   float(keys[0].get('value').split()[0]) / 2, places=4)
        dtd = os.environ.get('CY_TEST_FCPXML_DTD')
        if dtd:
            subprocess.run(['xmllint', '--noout', '--dtdvalid', dtd, str(path)], check=True)

    def test_long_propose_approve_and_build(self):
        self.media_fixture()
        project = self.base / 'project'
        state = self.base / 'state'
        config = write_json(self.base / 'runtime.json', {
            'schema_version': 2, 'python': sys.executable,
            'ffmpeg': shutil.which('ffmpeg'), 'backend': 'none',
        })
        global_flags = ('--config', config, '--state-dir', state)
        self.editor('long', *global_flags, 'init', '--project', project, '--name', '长片',
                    '--media', self.source)
        manifest = json.loads((project / 'project.json').read_text(encoding='utf-8'))
        sid = next(iter(manifest['sources']))
        self.editor('long', *global_flags, 'import-transcript', '--project', project, '--input', self.srt)
        templates = self.base / 'templates'
        self.editor('long', *global_flags, 'templates', '--project', project, '--out', templates)
        template = json.loads((templates / 'proposal.json').read_text(encoding='utf-8'))['candidates'][0]
        self.assertEqual([template[key] for key in TITLES], [[['', '']] * 3] * 2)
        transcript = json.loads((project / 'transcript.json').read_text(encoding='utf-8'))
        ids = [unit['id'] for unit in transcript['units']]
        candidate = dict(TITLES, title='口播剪辑', thesis='先理清主线，再删重复', reason='保留方法',
                         unit_ids=ids, keep_pause_units=ids, excluded=[],
                         chapters=[dict(title='主线', start=ids[0]), dict(title='去重', start=ids[1])])
        proposal_data = {
            'mode': 'multi', 'reviewed_sources': [sid],
            'candidates': [
                dict(candidate, id='topic-a', **LEGACY_TITLES),
                dict(candidate, id='topic-b', title='另一个话题'),
            ],
        }
        proposal = write_json(self.base / 'proposal.json', proposal_data)
        reply = self.base / 'reply.txt'
        reply.write_text('合成测试，按两条做', encoding='utf-8')
        propose = ('propose', '--project', project, '--input', proposal)
        approve = ('approve', '--project', project, '--selected', 'topic-a,topic-b', '--reply-file', reply)
        build = ('build', '--project', project, '--version', 'v1', '--skip-recheck')
        self.editor('long', *global_flags, *propose)
        self.editor('long', *global_flags, *approve)
        self.assertIn('补到三组', self.editor('long', *global_flags, *build, expected_code=2))
        self.assertFalse((project / 'plans/v1.json').exists())

        old_plan = project / 'plans/v1.json'
        self.editor('long', *global_flags, 'build-plan', '--project', project, '--out', old_plan)
        self.editor('long', *global_flags, 'validate-plan', '--project', project, '--plan', old_plan)
        old_bytes = old_plan.read_bytes()
        self.assertIn('补到三组', self.editor('long', *global_flags, *build, '--plan', old_plan, expected_code=2))
        self.assertIn('补到三组', self.editor('long', *global_flags, *build, expected_code=2))
        self.assertFalse((project / 'deliveries/v1').exists())
        self.assertFalse((project / 'records/v1').exists())
        self.assertFalse((project / 'assets/v1').exists())

        proposal_data['candidates'][0].update(TITLES)
        write_json(proposal, proposal_data)
        self.editor('long', *global_flags, *propose)
        self.editor('long', *global_flags, *approve)
        new_plan = project / 'plans/v1-updated.json'
        self.editor('long', *global_flags, 'build-plan', '--project', project, '--out', new_plan)
        self.editor('long', *global_flags, *build, '--plan', new_plan)
        self.assert_delivery(project / 'deliveries/v1', 2)
        self.assertEqual(old_plan.read_bytes(), old_bytes)

    def test_short_prepare_and_compile_multi(self):
        self.media_fixture()
        workdir = self.base / 'work'
        self.editor('short', 'prepare', '--sources', self.base, '--workdir', workdir)
        plan = json.loads((workdir / '剪辑计划_模板.json').read_text(encoding='utf-8'))
        self.assertEqual([plan[key] for key in TITLES], [[['', '']] * 3] * 2)
        plan.update(LEGACY_TITLES)
        segment = plan['segments'][0]
        ids = [*segment['evidence_unit_ids'], 'u000002']
        segment.update(reason='保留剪辑方法', evidence_unit_ids=ids, keep_pauses=True)
        plan['chapters'] = [dict(title='主线', start=ids[0]),
                            dict(title='去重', start=f"{segment['source']}:{ids[1]}")]
        plan_path = write_json(workdir / 'plan.json', plan)
        self.editor('short', 'validate-multi', '--plan', plan_path)
        compile_args = ('compile-multi', '--plan', plan_path, '--out', self.output, '--no-recheck')
        self.assertIn('补到三组', self.editor('short', *compile_args, expected_code=2))
        self.assertFalse(self.output.exists())
        self.assertFalse((workdir / '编译记录').exists())

        plan.update(TITLES)
        write_json(plan_path, plan)
        self.editor('short', *compile_args)
        self.assert_delivery(self.output, 1)


if __name__ == '__main__':
    unittest.main()
