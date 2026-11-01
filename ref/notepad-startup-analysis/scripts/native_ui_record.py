"""Read menu labels and toolbar positions; never invoke a command or send input."""
import ctypes as C
from ctypes import wintypes as W
import win32_probe as win

win.bind(win.U, 'GetMenu', W.HMENU, W.HWND)
win.bind(win.U, 'GetMenuItemCount', C.c_int, W.HMENU)
win.bind(win.U, 'GetSubMenu', W.HMENU, W.HMENU, C.c_int)
win.bind(win.U, 'GetMenuItemID', W.UINT, W.HMENU, C.c_int)
win.bind(win.U, 'GetMenuStringW', C.c_int, W.HMENU, W.UINT, W.LPWSTR, C.c_int, W.UINT)


class MenuInfo(C.Structure):
    _fields_ = [('cbSize', W.DWORD), ('fMask', W.DWORD), ('dwStyle', W.DWORD),
        ('cyMax', W.UINT), ('hbrBack', W.HBRUSH), ('dwContextHelpID', W.DWORD),
        ('dwMenuData', C.c_size_t)]


win.bind(win.U, 'GetMenuInfo', W.BOOL, W.HMENU, C.POINTER(MenuInfo))


def record(hwnd):
    leaves = []

    def menu_items(menu, path=()):
        items = []
        info = MenuInfo()
        info.cbSize, info.fMask = C.sizeof(info), 0x10  # MIM_STYLE
        assert win.U.GetMenuInfo(menu, C.byref(info))
        for index in range(win.U.GetMenuItemCount(menu)):
            text = C.create_unicode_buffer(1024)
            win.U.GetMenuStringW(menu, index, text, len(text), 0x400)
            submenu = win.U.GetSubMenu(menu, index)
            item = {'text': text.value, 'id': win.U.GetMenuItemID(menu, index),
                'menu_handle': menu, 'position': index, 'menu_style': info.dwStyle}
            if submenu:
                item['children'] = menu_items(submenu, (*path, text.value))
            elif item['id'] not in (0, 0xffffffff):
                leaves.append((item['id'], [*path, text.value]))
            items.append(item)
        return items

    menus = menu_items(win.U.GetMenu(hwnd))
    toolbars = []
    for child in win.windows(parent=hwnd):
        if child['class'] != 'ToolbarWindow32':
            continue
        toolbar = child['hwnd']
        buttons = []
        for command, path in leaves:
            index = win.message(toolbar, 0x400+25, command)
            if index is not None and index < 0x7fffffff:
                buttons.append({'index': index, 'menu_path': path,
                                'command': command, 'state': win.message(toolbar, 0x400+18, command)})
        toolbars.append({'visible': child['visible'], 'count': win.message(toolbar, 0x400+24),
                         'buttons': sorted(buttons, key=lambda item: item['index'])})
    return {'menus': menus, 'toolbars': toolbars}
