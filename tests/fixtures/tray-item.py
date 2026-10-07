#!/usr/bin/env python3
"""Synthetic StatusNotifierItem; refuses any bus except this test's private one."""
import os
from pathlib import Path
import sys
import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib
root=Path(sys.argv[1])
assert os.environ['DBUS_SESSION_BUS_ADDRESS'].startswith('unix:path='+str(Path(os.environ['XDG_RUNTIME_DIR']) / 'bus'))
bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
props={
 'Category':('s','ApplicationStatus'),'Id':('s','emaki-fixture'),'Title':('s','PRIVATE_TRAY'),
 'Status':('s','Active'),'WindowId':('u',0),'IconName':('s',''),
 'IconPixmap':('a(iiay)',[(16,16,bytes([255,244,128,24])*256)]),'OverlayIconName':('s',''),'OverlayIconPixmap':('a(iiay)',[]),
 'AttentionIconName':('s',''),'AttentionIconPixmap':('a(iiay)',[]),'AttentionMovieName':('s',''),
 'ToolTip':('(sa(iiay)ss)',('',[],'PRIVATE_TRAY','Fixture')),'ItemIsMenu':('b',False),'Menu':('o','/'),
}
xml='<node><interface name="org.kde.StatusNotifierItem">'+''.join(f'<property name="{n}" type="{s}" access="read"/>' for n,(s,v) in props.items())+'<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method></interface></node>'
def call(c,s,p,i,m,params,invocation):
    (root/'tray-activated').write_text(m)
    invocation.return_value(GLib.Variant('()',()))
info=Gio.DBusNodeInfo.new_for_xml(xml)
bus.register_object('/StatusNotifierItem', info.interfaces[0], call, lambda c,s,p,i,n: GLib.Variant(*props[n]), None)
menu_xml = '<node><interface name="com.canonical.dbusmenu"><property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/><property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/><method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/><arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method><method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method><method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/></method></interface></node>'
menu_props={'Version':('u',4),'TextDirection':('s','ltr'),'Status':('s','normal'),'IconThemePath':('as',[])}
def menu_call(c,s,p,i,m,params,invocation):
    if m=='GetLayout':
        parent=params.unpack()[0]
        leaf=GLib.Variant('(ia{sv}av)',(3,{'label':GLib.Variant('s','Nested fixture'),'enabled':GLib.Variant('b',True),'visible':GLib.Variant('b',True)},[]))
        sub=(2,{'label':GLib.Variant('s','PRIVATE_SUB'),'enabled':GLib.Variant('b',True),'visible':GLib.Variant('b',True),'children-display':GLib.Variant('s','submenu')},[leaf])
        if parent==2:
            invocation.return_value(GLib.Variant('(u(ia{sv}av))',(1,sub)));return
        child=GLib.Variant('(ia{sv}av)',(1,{'label':GLib.Variant('s','Open fixture'),'enabled':GLib.Variant('b',True),'visible':GLib.Variant('b',True)},[]))
        invocation.return_value(GLib.Variant('(u(ia{sv}av))',(1,(0,{'children-display':GLib.Variant('s','submenu')},[child,GLib.Variant('(ia{sv}av)',sub)]))))
    elif m=='AboutToShow':invocation.return_value(GLib.Variant('(b)',(False,)))
    elif m=='Event':
        if params.unpack()[1]=='clicked':(root/'tray-activated').write_text('clicked' if params.unpack()[0]==1 else 'clicked-'+str(params.unpack()[0]))
        invocation.return_value(GLib.Variant('()',()))
menu_info=Gio.DBusNodeInfo.new_for_xml(menu_xml)
bus.register_object('/',menu_info.interfaces[0],menu_call,lambda c,s,p,i,n: GLib.Variant(*menu_props[n]),None)
bus.call_sync('org.kde.StatusNotifierWatcher','/StatusNotifierWatcher','org.kde.StatusNotifierWatcher','RegisterStatusNotifierItem',GLib.Variant('(s)',('/StatusNotifierItem',)),None,Gio.DBusCallFlags.NONE,2000,None)
GLib.MainLoop().run()
