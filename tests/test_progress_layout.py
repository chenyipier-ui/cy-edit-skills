"""Chapter bar timing, placement and editable label regressions."""
from fractions import Fraction
import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET


SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/cy-edit-longvideo/scripts'
SPEC = importlib.util.spec_from_file_location('progress_packaging', SCRIPTS / 'cy_packaging.py')
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


def seconds(element, attr):
    return Fraction(element.get(attr).removesuffix('s'))


class ProgressLayout(unittest.TestCase):
    def setUp(self):
        assets = tempfile.TemporaryDirectory()
        self.addCleanup(assets.cleanup)
        self.assets = Path(assets.name)
        self.frame = Fraction(1001, 30000)

    def document(self, width=1920, height=1080, *, end_frame=3902, first='s2'):
        xml = ET.Element('fcpxml', version='1.10')
        ET.SubElement(xml, 'resources')
        spine = ET.SubElement(xml, 'spine')
        hosts = []
        bounds = (0, 302, 1202, 2572, end_frame)
        for index, (left, right) in enumerate(zip(bounds, bounds[1:]), 1):
            clip = ET.SubElement(spine, 'asset-clip')
            hosts.append(dict(node=clip, id=f's{index}', start=(index*211)*self.frame,
                              offset=left*self.frame, duration=(right-left)*self.frame))
        hosts[0]['unit_ids'] = ['u900']  # Earlier overlap should only supply fallback timing.
        hosts[1]['unit_offsets'] = {'u450': 450*self.frame, 'u900': 900*self.frame}
        hosts[2]['unit_offsets'] = {'u900': 1203*self.frame}
        copy = {
            'content_title': ['主题放在下方', '章节位于上方'],
            'cover_title': ['封面第一行', '封面第二行'],
            'chapters': [{'title': '起点', 'start': first},
                         {'title': '较长章节名称' * 5, 'start': 'u900'},
                         {'title': 'AI流程', 'start': 's4'}],
        }
        notes = packaging.decorate(xml, hosts, [], copy, self.assets, width, height, self.frame)
        return xml, hosts, notes

    def test_fractional_cut_start_and_unequal_chapter_timing(self):
        xml, hosts, _ = self.document()
        fills = xml.findall('.//video[@ref="cyBarFill"]')
        self.assertEqual(len(fills), 1)
        fill = fills[0]
        self.assertIn(fill, list(hosts[1]['node']))
        self.assertEqual(seconds(fill, 'offset'), hosts[1]['start'])
        self.assertEqual(seconds(fill, 'duration'), (3902-302)*self.frame)
        keyframes = fill.findall('.//keyframe')
        self.assertEqual([seconds(key, 'time') for key in keyframes],
                         [(frame-302)*self.frame for frame in (302, 900, 2572, 3901)])
        self.assertEqual({(key.get('interp'), key.get('curve')) for key in keyframes},
                         {('linear', 'linear')})
        for index, key in enumerate(keyframes):
            x = float(key.get('value').split()[0])
            self.assertAlmostEqual(1 + x/(1920/1080*100), index/3, places=6)

    def test_bar_direction_cell_centres_and_adaptive_editable_labels(self):
        for width, height, direction in ((1920, 1080, 1), (1080, 1920, -1)):
            with self.subTest(canvas=(width, height)):
                xml, _, notes = self.document(width, height)
                bar = xml.find('.//video[@ref="cyBarBG"]/adjust-transform')
                fill = xml.find('.//video[@ref="cyBarFill"]/adjust-transform')
                bar_y = float(bar.get('position').split()[1])
                fill_y = float(fill.get('position').split()[1])
                self.assertGreater(bar_y*direction, 0)
                self.assertLess(fill_y, bar_y)
                self.assertIsNotNone(xml.find('.//video[@ref="cyBarGrid"]'))
                for name in ('cyBarBG', 'cyBarFill', 'cyBarGrid'):
                    self.assertEqual(len(xml.findall(f'.//video[@ref="{name}"]')), 1)
                sizes = []
                labels = [node for node in xml.iter('title') if node.get('name').startswith('进度条章节')]
                self.assertEqual(len(labels), 3)
                for index, label in enumerate(labels):
                    x, y = map(float, label.find('adjust-transform').get('position').split())
                    self.assertAlmostEqual(x, (index-1)*width/(3*height)*100, delta=.051)
                    self.assertGreater(y, bar_y)
                    sizes.append(float(label.find('text-style-def/text-style').get('fontSize')))
                self.assertIn('\u2004', labels[0].find('text/text-style').text)
                self.assertEqual(labels[2].find('text/text-style').text, 'AI流程')
                self.assertLess(sizes[1], sizes[0])
                self.assertTrue(any('较小字号' in note for note in notes))
                self.assertLessEqual(sizes[1] * height/1080 * 30, .84*width/3)
                if direction == 1:
                    layout = packaging.layout_for(width, height)
                    self.assertEqual((layout['sub_size'], layout['title_size'], layout['cover_size']), (54, 48, 80))
                    self.assertEqual(sizes[0], 28)
                    title_y = float(xml.find('.//title[@name="内容标题"]/adjust-transform').get('position').split()[1])
                    bar_bottom = bar_y - layout['bar_h']/height*50
                    title_top = title_y + packaging.LINE_H*layout['title_size']/height*100
                    self.assertLess(title_top, bar_bottom)

    def test_first_chapter_can_start_inside_an_existing_clip(self):
        xml, hosts, _ = self.document(first='u450')
        fill = xml.find('.//video[@ref="cyBarFill"]')
        self.assertIn(fill, list(hosts[1]['node']))
        expected_offset = hosts[1]['start'] + (450-302)*self.frame
        self.assertEqual(seconds(fill, 'offset'), expected_offset)
        self.assertEqual(seconds(xml.find('.//title[@name="进度条章节 1"]'), 'offset'), expected_offset)

    def test_short_project_keeps_bar_assets_out(self):
        xml, _, _ = self.document(end_frame=3596)
        self.assertIsNone(xml.find('.//video[@ref="cyBarBG"]'))
        self.assertFalse(list(self.assets.glob('*.png')))


if __name__ == '__main__':
    unittest.main()
