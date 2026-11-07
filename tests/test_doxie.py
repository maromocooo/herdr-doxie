import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('doxie', str(ROOT / 'herdr-doxie'))
spec = importlib.util.spec_from_loader(loader.name, loader)
d = importlib.util.module_from_spec(spec)
loader.exec_module(d)


class FakeApi:
    path = '/unused'
    def __init__(self):
        self.writes = []
        self.fail = False
    def agents(self): return {'w1:p1': ('working', 'w1')}
    def panes(self): return {'w1:p1', 'w1:p2'}
    def workspaces(self): return {'w1'}
    def report(self, p, labels):
        if self.fail: raise d.ApiError('rejected', 'test')
        self.writes.append(('pane', p, labels))
    def report_space(self, ws, text):
        if self.fail: raise d.ApiError('rejected', 'test')
        self.writes.append(('space', ws, text))


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='doxie-test-')
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'XDG_RUNTIME_DIR': self.tmp.name,
            'HERDR_PLUGIN_CONFIG_DIR': self.tmp.name, 'HERDR_SOCKET_PATH': self.tmp.name + '/herdr.sock',
            'HERDR_CONFIG_PATH': self.tmp.name + '/config.toml',
            'HERDR_DOXIE_ANIMATE': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        Path(os.environ['HERDR_CONFIG_PATH']).write_text('[ui.sidebar.spaces]\nrows = [["workspace"]]\n')

    def test_spacing_preserves_comments_arrays_strings_and_other_tables(self):
        source = ('onboarding = false\n# [ui.sidebar.spaces]\n'
                  '[ui.sidebar.agents]\nrow_gap = 2\n'
                  '[ui.sidebar.spaces] # keep\nrows = [\n ["workspace"],\n ["branch"]\n]\n'
                  '  row_gap = 0 # compact\n[terminal]\ndefault_shell = "zsh"\n')
        current, updated = d.space_gap_config(source, 1)
        self.assertEqual(current, 0)
        self.assertEqual(updated, source.replace('row_gap = 0 # compact', 'row_gap = 1 # compact'))
        self.assertEqual(d.space_gap_config(updated, 0)[1], source)
        # Table-looking text inside multiline strings and arrays is never a table.
        for quote in ('"""', "'''"):
            text = 'example = ' + quote + '\n[ui.sidebar.spaces]\nrow_gap = 9\n' + quote + '\n'
            with self.assertRaises(ValueError): d.space_gap_config(text, 1)
            self.assertEqual(d.space_gap_config(text + source, 1)[1], text + updated)
        for ending in ('\n', '\r\n', ''):
            text = '[ui.sidebar.spaces]' + ending
            self.assertIn('row_gap = 1', d.space_gap_config(text, 1)[1])
        with self.assertRaises(ValueError): d.space_gap_config('[ui.sidebar.spaces]\n'*2, 1)

    def test_spacing_validates_backs_up_preserves_symlinks_and_reloads(self):
        if not shutil.which('herdr'): self.skipTest('Herdr CLI not installed')
        path = d.herdr_config_path()
        source = (ROOT/'config-snippet.toml').read_bytes()
        path.write_bytes(source)
        path.chmod(0o640)
        link = path.with_name('linked.toml'); link.symlink_to(path)
        with patch.object(d.Api, 'call', return_value={}) as call:
            gap, notice = d.toggle_space_gap(link)
        self.assertEqual(gap, 0)
        self.assertTrue(link.is_symlink())
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertIn('reloaded', notice)
        call.assert_called_once_with('server.reload_config', {})
        backups = list(path.parent.glob('.config.toml.doxie-backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), source)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(d.read_space_gap(path), 0)
        with patch.object(d.Api, 'call', side_effect=ConnectionError('offline')):
            gap, notice = d.toggle_space_gap(path)
        self.assertEqual(gap, 1)
        self.assertIn('Saved; reload failed', notice)
        self.assertEqual(path.read_bytes(), source)

    def test_spacing_rejected_or_concurrent_config_never_overwritten(self):
        path = d.herdr_config_path(); source = path.read_bytes()
        rejected = subprocess.CompletedProcess([], 0, 'config: invalid', '')
        with patch.object(d.subprocess, 'run', return_value=rejected), patch.object(d.Api, 'call') as call:
            with self.assertRaisesRegex(ValueError, 'rejected'): d.toggle_space_gap(path)
            call.assert_not_called()
        self.assertEqual(path.read_bytes(), source)
        def validate(*args, **kwargs):
            path.write_bytes(source + b'# external edit\n')
            return subprocess.CompletedProcess([], 0, 'config: ok\n', '')
        with patch.object(d.subprocess, 'run', side_effect=validate):
            with self.assertRaisesRegex(ValueError, 'changed'): d.toggle_space_gap(path)
        self.assertEqual(path.read_bytes(), source + b'# external edit\n')
        self.assertFalse(list(path.parent.glob('.doxie-config-*')))
        self.assertFalse(list(path.parent.glob('.config.toml.doxie-backup-*')))

    def test_spacing_tui_toggle_is_independent_of_animation_preferences(self):
        class Screen:
            def __init__(self):
                self.keys = iter([ord('k'), 32, ord('q')])
                self.lines = []
            def timeout(self, *a): pass
            def erase(self): pass
            def getmaxyx(self): return (12, 100)
            def addnstr(self, y, x, text, width): self.lines.append(text)
            def refresh(self): pass
            def getch(self): return next(self.keys)
        screen = Screen()
        valid = subprocess.CompletedProcess([], 0, 'config: ok\n', '')
        with patch.object(d.curses, 'curs_set'), patch.object(d, 'ui_theme'), \
             patch.object(d.subprocess, 'run', return_value=valid), patch.object(d.Api, 'call'):
            d.settings_ui(screen, threading.Event(), ['Settings'])
        self.assertEqual(d.read_space_gap(d.herdr_config_path()), 1)
        self.assertEqual(d.read_settings(), d.DEFAULTS)
        self.assertFalse(d.settings_path().exists())
        self.assertTrue(any('Spaces spacing: ON' in line for line in screen.lines))

    def test_bundled_font_names_and_checksum(self):
        data=(ROOT/'HerdrDoxie.ttf').read_bytes()
        padded=data+b'\0'*(-len(data)%4)
        self.assertEqual(sum(struct.unpack('>'+str(len(padded)//4)+'I',padded))&0xffffffff,0xB1B0AFBA)
        count=struct.unpack_from('>H',data,4)[0]
        tables={}
        for i in range(count):
            tag,checksum,offset,length=struct.unpack_from('>4sIII',data,12+16*i)
            tables[tag]=data[offset:offset+length]
        names=tables[b'name'];_,count,storage=struct.unpack_from('>HHH',names)
        decoded={}
        for i in range(count):
            platform,encoding,lang,nid,length,offset=struct.unpack_from('>6H',names,6+12*i)
            if platform==3:
                decoded[nid]=names[storage+offset:storage+offset+length].decode('utf-16-be')
        self.assertEqual(decoded[1],'Herdr Doxie')
        self.assertEqual(decoded[6],'HerdrDoxie-Regular')
        self.assertIn('MIT',decoded[13])

    def test_animation_combinations(self):
        for master in (False, True):
            for running in (False, True):
                for sleeping in (False, True):
                    settings = dict(animate=master, running=running, sleeping=sleeping)
                    frames = [d.labels_for('w1:p1', i*.25, settings) for i in range(30)]
                    self.assertEqual(len({f['working'] for f in frames}) > 1, master and running)
                    self.assertEqual(len({f['idle'] for f in frames}) > 1, master and sleeping)
                    self.assertEqual({f['done'] for f in frames}, {d.HOUSE})

    def test_space_priority_and_master(self):
        s = dict(d.DEFAULTS, animate=False)
        self.assertEqual(d.space_icon('w1', set(), 0, s), d.HOUSE_EMPTY)
        self.assertEqual(d.space_icon('w1', {'blocked','working'}, 0, s), d.SIT)
        self.assertEqual(d.space_icon('w1', {'done'}, 0, s), d.SLEEP_STILL)

    def test_settings_roundtrip_and_bad_input(self):
        s = dict(d.DEFAULTS, animate=False, sleeping=False)
        d.save_settings(s)
        self.assertEqual(d.read_settings(), s)
        d.settings_path().write_text('{"animate":"false"}')
        with self.assertRaises(ValueError): d.read_settings()

    def test_intervals_and_help(self):
        for v in ['0', '-1', 'nan', 'inf', 'abc', '0.01']:
            with patch.dict(os.environ, {'HERDR_DOXIE_INTERVAL':v}):
                with self.assertRaises(ValueError): d.interval('HERDR_DOXIE_INTERVAL','.25')
        p = subprocess.run([sys.executable, str(ROOT/'herdr-doxie'), '--help'],
            env={**os.environ, 'HERDR_DOXIE_INTERVAL':'0'}, capture_output=True)
        self.assertEqual(p.returncode, 0)

    def test_old_settings_migration_and_new_validation(self):
        d.settings_path().write_text('{"animate":false,"running":true,"sleeping":false}')
        settings=d.read_settings()
        self.assertEqual(settings,dict(d.DEFAULTS,animate=False,sleeping=False))
        for key,value in [('run_interval',0),('run_interval',True),('sleep_interval',float('nan')),
                          ('sleep_interval',61),('theme','auto')]:
            with self.assertRaises(ValueError): d.save_settings(dict(settings,**{key:value}))
        self.assertEqual(d.read_settings(),settings) # rejected writes leave the file intact

    def test_speed_limits_and_environment_precedence(self):
        settings=dict(d.DEFAULTS,run_interval=.1,sleep_interval=60)
        self.assertEqual(d.adjust_setting(settings,'run_interval',-1)['run_interval'],.1)
        self.assertEqual(d.adjust_setting(settings,'sleep_interval',1)['sleep_interval'],60)
        self.assertEqual(d.adjust_setting(settings,'run_interval',1)['run_interval'],.15)
        with patch.dict(os.environ,{'HERDR_DOXIE_INTERVAL':'.5','HERDR_DOXIE_SLEEP_INTERVAL':'1.5'}):
            self.assertEqual(d.effective_intervals(settings),(.5,1.5))
        self.assertEqual(settings['run_interval'],.1)

    def test_light_theme_covers_every_state_and_empty_space(self):
        dark=dict(d.DEFAULTS);light=dict(d.DEFAULTS,theme='light')
        for now in (0,.25,.7,2.1):
            a=d.labels_for('w1:p1',now,dark);b=d.labels_for('w1:p1',now,light)
            for state in a:self.assertEqual([ord(c)+32 for c in a[state]],[ord(c) for c in b[state]])
        self.assertEqual(d.space_icon('w1',set(),0,light),d.themed(d.HOUSE_EMPTY,light))

    def test_font_aliases_and_palette_rules_agree(self):
        data=(ROOT/'HerdrDoxie.ttf').read_bytes();tables={}
        for i in range(struct.unpack_from('>H',data,4)[0]):
            tag,_,off,size=struct.unpack_from('>4sIII',data,12+16*i);tables[tag]=data[off:off+size]
        cmap=tables[b'cmap'];mappings=[]
        for i in range(struct.unpack_from('>H',cmap,2)[0]):
            _,_,off=struct.unpack_from('>HHI',cmap,4+8*i)
            if struct.unpack_from('>H',cmap,off)[0]!=12:continue
            mapping={}
            for j in range(struct.unpack_from('>I',cmap,off+12)[0]):
                a,b,gid=struct.unpack_from('>III',cmap,off+16+12*j)
                mapping.update({cp:gid+cp-a for cp in range(a,b+1)})
            mappings.append(mapping)
        self.assertTrue(mappings)
        for mapping in mappings:
            for cp in range(d.BASE,d.BASE+18):self.assertEqual(mapping[cp],mapping[cp+32])
        config=(ROOT/'config-snippet.toml').read_text()
        # Agents never show the empty house; Spaces never show the done house.
        # Excluding those rules keeps each token within Herdr's 16-rule limit.
        for base in (d.BASE,d.BASE+32):
            for i in range(9):self.assertEqual(config.count('\\U%08X'%(base+2*i)),1 if i in (4,8) else 2)

    def test_herdr_accepts_palette_config(self):
        herdr=shutil.which('herdr')
        if not herdr:self.skipTest('Herdr CLI not installed')
        result=subprocess.run([herdr,'config','check'],capture_output=True,text=True,
            env={**os.environ,'HERDR_CONFIG_PATH':str(ROOT/'config-snippet.toml')})
        # Herdr 0.9.5 can exit zero even when the configuration is rejected.
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'config: ok',result.stdout+result.stderr)

    def test_runner_uses_changed_speed_and_theme_without_restart(self):
        seen=[];stop=threading.Event();api=FakeApi()
        api.clear=lambda p:None;api.clear_space=lambda w:None
        class Renderer:
            def __init__(self,api):pass
            def scan(self):pass
            def render(self,now,settings,run,sleep):seen.append((run,sleep,settings['theme']))
        class Events:
            def __init__(self,path):self.n=0
            def wait(self,timeout):
                self.n+=1
                if self.n==1:d.save_settings(dict(d.DEFAULTS,run_interval=.1,sleep_interval=1.5,theme='light'))
                else:stop.set()
                return False
            def close(self):pass
        with patch.object(d,'Api',return_value=api),patch.object(d,'Renderer',Renderer),patch.object(d,'Events',Events):
            d.run(stop)
        self.assertEqual(seen,[(.25,.7,'dark'),(.1,1.5,'light')])

    def test_tui_speed_theme_keys_and_persistence(self):
        class Screen:
            def __init__(self):self.keys=iter([ord('j')]*3+[d.curses.KEY_LEFT,ord('j'),d.curses.KEY_RIGHT,ord('j'),32,ord('q')])
            def timeout(self,*a):pass
            def erase(self):pass
            def getmaxyx(self):return (12,80) # compact popup layout
            def addnstr(self,*a):pass
            def refresh(self):pass
            def getch(self):return next(self.keys)
        with patch.object(d.curses,'curs_set'),patch.object(d,'ui_theme'):
            d.settings_ui(Screen(),threading.Event(),['Settings'])
        self.assertEqual(d.read_settings(),dict(d.DEFAULTS,run_interval=.2,sleep_interval=1,theme='light'))

    def test_not_found_is_semantic(self):
        for code in ['not_found','pane_not_found','workspace_not_found']:
            with self.assertRaises(d.NotFound): d.checked({'error':{'code':code}})
        with self.assertRaises(d.ApiError): d.checked({'error':{'code':'invalid_params'}})

    def test_endpoint_locks(self):
        a,b = d.Instance(),d.Instance()
        self.addCleanup(a.close); self.addCleanup(b.close)
        self.assertTrue(a.acquire()); self.assertFalse(b.acquire())
        with patch.dict(os.environ, {'HERDR_SOCKET_PATH':self.tmp.name+'/other.sock'}):
            c = d.Instance(); self.addCleanup(c.close)
            self.assertTrue(c.acquire()); self.assertNotEqual(a.stop_path,c.stop_path)

    def test_cooperative_stop(self):
        a = d.Instance(); self.assertTrue(a.acquire())
        def runner():
            while not a.stop_path.exists(): time.sleep(.01)
            a.close()
        t=threading.Thread(target=runner); t.start()
        b=d.Instance()
        try: b.stop_and_acquire(); self.assertFalse(t.is_alive() and not a.stop_path.exists())
        finally: b.close(); t.join(1)

    def test_failed_preparation_is_retried_and_refreshed(self):
        api=FakeApi(); r=d.Renderer(api); r.scan(); api.fail=True
        with self.assertRaises(d.ApiError): r.render(0,d.DEFAULTS,.25,.7)
        self.assertEqual(r.sent,{})
        api.fail=False; r.render(1,d.DEFAULTS,.25,.7)
        self.assertIn(('pane','w1:p2'),r.sent)
        count=len([w for w in api.writes if w[1]=='w1:p2'])
        r.render(22,d.DEFAULTS,.25,.7)
        self.assertEqual(len([w for w in api.writes if w[1]=='w1:p2']),count+1)

    def test_ttl_and_clear_payload(self):
        api=d.Api()
        with patch.object(api,'call') as call:
            api.report('w1:p1',{}); self.assertEqual(call.call_args.args[1]['ttl_ms'],60000)
            api.report_space('w1','dog'); self.assertEqual(call.call_args.args[1]['ttl_ms'],60000)
            api.clear_space('w1'); self.assertEqual(call.call_args.args[1]['tokens'],{'doxie':None})

    def test_event_fragmentation_eof_and_errors(self):
        a,b=socket.socketpair();self.addCleanup(a.close);self.addCleanup(b.close)
        stream=d.Events.__new__(d.Events); stream.sock=a; stream.buffer=b''
        b.sendall(b'{"event":');self.assertFalse(stream.wait(.1))
        b.sendall(b'"pane.created"}\n');self.assertTrue(stream.wait(.1))
        b.sendall(b'{"error":{"code":"events_lost"}}\n')
        with self.assertRaises(d.ApiError):stream.wait(.1)
        b.close()
        with self.assertRaises(ConnectionError):stream.wait(.1)

    def test_runner_stops_and_clears_with_live_settings(self):
        api=FakeApi()
        api.clear=lambda p: api.writes.append(('clear',p,None))
        api.clear_space=lambda w: api.writes.append(('clear_space',w,None))
        stop=threading.Event()
        closed=[]
        class Stream:
            def __init__(self,path):self.n=0
            def wait(self,timeout):
                self.n+=1
                if self.n==1: d.save_settings(dict(d.DEFAULTS,animate=False))
                else: stop.set()
                return False
            def close(self):closed.append(True)
        with patch.object(d,'Api',return_value=api),patch.object(d,'Events',Stream):
            d.run(stop)
        self.assertTrue(closed)
        self.assertIn(('clear','w1:p1',None),api.writes)
        frames=[w[2] for w in api.writes if w[:2]==('pane','w1:p1')]
        self.assertEqual(frames[-1]['working'],d.RUN[0])
        self.assertEqual(frames[-1]['idle'],d.SLEEP_STILL)
        probe=d.Instance()
        try:self.assertTrue(probe.acquire())
        finally:probe.close()

    def test_runner_eof_exits_without_reconnect(self):
        api=FakeApi();closed=[]
        class Stream:
            def __init__(self,path):pass
            def wait(self,timeout):raise ConnectionError('EOF')
            def close(self):closed.append(True)
        with patch.object(d,'Api',return_value=api),patch.object(d,'Events',Stream) as stream:
            with self.assertRaises(ConnectionError):d.run(threading.Event())
        self.assertTrue(closed)
        probe=d.Instance()
        try:self.assertTrue(probe.acquire())
        finally:probe.close()

    def test_missing_server_clear_exits_nonzero(self):
        p=subprocess.run([sys.executable,str(ROOT/'herdr-doxie'),'--clear'],capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0)
        self.assertIn('herdr-doxie:',p.stderr)

    def test_live_tui_keyboard_save(self):
        class Screen:
            def __init__(self):self.keys=iter([32,d.curses.KEY_DOWN,32,ord('q')])
            def timeout(self,*a):pass
            def erase(self):pass
            def getmaxyx(self):return (24,80)
            def addnstr(self,*a):pass
            def refresh(self):pass
            def getch(self):return next(self.keys)
        with patch.object(d.curses,'curs_set'):
            d.settings_ui(Screen(),threading.Event(),['Running'])
        self.assertEqual(d.read_settings(),dict(d.DEFAULTS,animate=False,running=False,sleeping=True))


if __name__ == '__main__': unittest.main()
