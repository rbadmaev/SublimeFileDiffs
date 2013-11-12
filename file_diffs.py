# coding: utf8
import os
import re
import tempfile

import sublime
import sublime_plugin
import difflib

from fnmatch import fnmatch
from threading import Thread
from subprocess import Popen
import codecs

SETTINGS = sublime.load_settings('FileDiffs.sublime-settings')

CLIPBOARD = u'Diff file with Clipboard'
SELECTIONS = u'Diff Selections'
SAVED = u'Diff file with Saved'
FILE = u'Diff file with File in Project…'
TAB = u'Diff file with Open Tab…'


FILE_DIFFS = [CLIPBOARD, SAVED, FILE, TAB]


class FileDiffMenuCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        menu_items = FILE_DIFFS[:]
        saved = SAVED
        non_empty_regions = [region for region in self.view.sel() if not region.empty()]
        if len(non_empty_regions) == 2:
            menu_items.insert(1, SELECTIONS)
        elif len(non_empty_regions):
            menu_items = [f.replace(u'Diff file', u'Diff selection') for f in menu_items]
            saved = saved.replace(u'Diff file', u'Diff selection')

        if not (self.view.file_name() and self.view.is_dirty()):
            menu_items.remove(saved)

        def on_done(index):
            restored_menu_items = [f.replace(u'Diff selection', u'Diff file') for f in menu_items]
            if index == -1:
                return
            elif restored_menu_items[index] == CLIPBOARD:
                self.view.run_command('file_diff_clipboard')
            elif restored_menu_items[index] == SELECTIONS:
                self.view.run_command('file_diff_selections')
            elif restored_menu_items[index] == SAVED:
                self.view.run_command('file_diff_saved')
            elif restored_menu_items[index] == FILE:
                self.view.run_command('file_diff_file')
            elif restored_menu_items[index] == TAB:
                self.view.run_command('file_diff_tab')
        sublime.set_timeout(lambda: self.view.window().show_quick_panel(menu_items, on_done), 10)

class DiffUnit:
    """docstring for DiffUnit"""
    def __init__(self, file_name = None, content = None, caption = None):
        super(DiffUnit, self).__init__()
        assert(file_name or content)
        self.__file_name = file_name
        self.__content = content
        self.__caption = caption
        self.__is_the_temp_file = False

    def __enter__(self):
        None

    def __exit__(self, type, value, traceback):
        if self.__is_the_temp_file:
            os.remove(self.__file_name)

    def file_name(self):
        if not self.__file_name:
            assert(self.__content)
            with tempfile.NamedTemporaryFile(delete=False) as f:
                self.__is_the_temp_file = True
                self.__file_name = f.name
                f.write(self.__content.encode('utf-8'))

        return self.__file_name

    def content(self):
        if not self.__content:
            assert(self.__file_name)
            with codecs.open(self.__file_name, mode='U', encoding='utf-8') as f:
                self.__content = f.readlines()

        return self.__content

    def caption(self):
        return self.__caption if self.__caption else self.file_name()


class FileDiffCommand(sublime_plugin.TextCommand):
    def diff_content(self):
        content = ''

        for region in self.view.sel():
            if region.empty():
                continue
            content += self.view.substr(region)

        if not content:
            if self.view.file_name() and not self.view.is_dirty():
                return DiffUnit(file_name=self.view.file_name())

            content = self.view.substr(sublime.Region(0, self.view.size()))

        return DiffUnit(caption=(self.view.file_name() if self.view.file_name() else self.view.name()) + u"_(Unsaved)",
                        content=content)

    def run_diff(self, unit1, unit2):
        if SETTINGS.get('cmd'):
            def run_excternal_diff_tool(unit1, unit2):
                with unit1, unit2:
                    command = SETTINGS.get('cmd')
                    assert(command)
                    command = [c.replace(u'$file1', unit1.file_name()) for c in command]
                    command = [c.replace(u'$file2', unit2.file_name()) for c in command]
                    command = [c.replace(u'$caption1', unit1.caption()) for c in command]
                    command = [c.replace(u'$caption2', unit2.caption()) for c in command]
                    process = Popen(command)
                    process.communicate()

            thread = Thread(target = run_excternal_diff_tool,
                            args = (unit1, unit2))
            thread.start()
            return

        diffs = list(difflib.unified_diff(  unit1.content(),
                                            unit2.content(),
                                            unit1.file_name(),
                                            unit2.file_name()))

        if not diffs:
            sublime.status_message('No Difference')
            return

        scratch = self.view.window().new_file()
        scratch.set_scratch(True)
        scratch.set_syntax_file('Packages/Diff/Diff.tmLanguage')
        scratch_edit = scratch.begin_edit('file_diffs')
        scratch.insert(scratch_edit, 0, ''.join(diffs))
        scratch.end_edit(scratch_edit)



class FileDiffClipboardCommand(FileDiffCommand):
    def run(self, edit, **kwargs):
        current = sublime.get_clipboard()
        diffs = self.run_diff(
                    self.diff_content(),
                    DiffUnit(caption='(clipboard)', content=sublime.get_clipboard()))


class FileDiffSelectionsCommand(FileDiffCommand):
    def run(self, edit, **kwargs):
        regions = self.view.sel()
        current = self.view.substr(regions[0])
        diff = self.view.substr(regions[1])

        # trim off indent
        indent = None
        for line in current.splitlines():
            new_indent = re.match('[ \t]*', line).group(0)
            if new_indent == '':
                continue

            if indent is None:
                indent = new_indent
            elif len(new_indent) < len(indent):
                indent = new_indent

            if not indent:
                break

        if indent:
            current = u"\n".join(line[len(indent):] for line in current.splitlines())

        # trim off indent
        indent = None
        for line in diff.splitlines():
            new_indent = re.match('[ \t]*', line).group(0)
            if new_indent == '':
                continue

            if indent is None:
                indent = new_indent
            elif len(new_indent) < len(indent):
                indent = new_indent

        if indent:
            diff = u"\n".join(line[len(indent):] for line in diff.splitlines())

        self.run_diff(
            DiffUnit(caption='first selection', content=current),
            DiffUnit(caption='second selection', content=diff))


class FileDiffSavedCommand(FileDiffCommand):
    def run(self, edit, **kwargs):
        self.run_diff(
            DiffUnit(file_name=self.view.file_name()),
            self.diff_content())


class FileDiffFileCommand(FileDiffCommand):
    def run(self, edit, **kwargs):
        common = None
        folders = self.view.window().folders()
        files = self.find_files(folders)
        for folder in folders:
            if common == None:
                common = folder
            else:
                common_len = len(common)
                while folder[0:common_len] != common[0:common_len]:
                    common_len -= 1
                    common = common[0:common_len]

        my_file = self.view.file_name()
        # filter out my_file
        files = [file for file in files if file != my_file]
        # shorten names using common length
        file_picker = [file[len(common):] for file in files]

        def on_done(index):
            if index > -1:
                self.run_diff(
                    self.diff_content(),
                    DiffUnit(file_name=files[index]))

        sublime.set_timeout(lambda: self.view.window().show_quick_panel(file_picker, on_done), 10)

    def find_files(self, folders):
        # Cannot access these settings!!  WHY!?
        # folder_exclude_patterns = self.view.settings().get('folder_exclude_patterns')
        # file_exclude_patterns = self.view.settings().get('file_exclude_patterns')
        folder_exclude_patterns = [".svn", ".git", ".hg", "CVS"]
        file_exclude_patterns = ["*.pyc", "*.pyo", "*.exe", "*.dll", "*.obj", "*.o", "*.a", "*.lib", "*.so", "*.dylib", "*.ncb", "*.sdf", "*.suo", "*.pdb", "*.idb", ".DS_Store", "*.class", "*.psd", "*.db"]

        ret = []
        for folder in folders:
            if not os.path.isdir(folder):
                continue

            for file in os.listdir(folder):
                fullpath = os.path.join(folder, file)
                if os.path.isdir(fullpath):
                    # excluded folder?
                    if not len([True for pattern in folder_exclude_patterns if fnmatch(file, pattern)]):
                        ret += self.find_files([fullpath])
                else:
                    # excluded file?
                    if not len([True for pattern in file_exclude_patterns if fnmatch(file, pattern)]):
                        ret.append(fullpath)
        return ret


class FileDiffTabCommand(FileDiffCommand):
    def run(self, edit, **kwargs):
        my_id = self.view.id()
        files = []
        contents = []
        untitled_count = 1
        for v in self.view.window().views():
            if v.id() != my_id:
                this_content = v.substr(sublime.Region(0, v.size()))
                if v.file_name():
                    files.append(v.file_name())
                elif v.name():
                    files.append(v.name())
                else:
                    files.append('untitled %d' % untitled_count)
                    untitled_count += 1

                contents.append(this_content)

        def on_done(index):
            if index > -1:
                self.run_diff(
                    self.diff_content(),
                    DiffUnit(file_name = files[index], content=contents[index]))

        if len(files) == 1:
            on_done(0)
        else:
            menu_items = [os.path.basename(f) for f in files]
            sublime.set_timeout(lambda: self.view.window().show_quick_panel(menu_items, on_done), 10)
