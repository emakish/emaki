#!/usr/bin/env python3
"""Synthetic player; only callable inside our isolated test bus."""
import json
import os
from pathlib import Path
import sys
import gi
gi.require_version('Gio','2.0')
from gi.repository import Gio, GLib
root=Path(sys.argv[1]); assert os.environ['DBUS_SESSION_BUS_ADDRESS'].startswith('unix:path='+str(Path(os.environ['XDG_RUNTIME_DIR']) / 'bus'))
name='org.mpris.MediaPlayer2.emaki_fixture'; player='org.mpris.MediaPlayer2.Player'; base='org.mpris.MediaPlayer2'
props={
base: {'Identity':('s','Fixture player'),'DesktopEntry':('s','fixture-player'),'CanQuit':('b',False),'CanRaise':('b',False),'HasTrackList':('b',False),'SupportedUriSchemes':('as',[]),'SupportedMimeTypes':('as',[])},
player: {'PlaybackStatus':('s','Playing'),'Metadata':('a{sv}',{'mpris:trackid':GLib.Variant('o','/fixture/track1'),'xesam:title':GLib.Variant('s','PRIVATE_TRACK_TITLE'),'xesam:artist':GLib.Variant('as',['PRIVATE_ARTIST'])}), 'CanControl':('b',True),'CanPlay':('b',True),'CanPause':('b',True),'CanSeek':('b',False),'CanGoNext':('b',True),'CanGoPrevious':('b',True),'LoopStatus':('s','None'),'Rate':('d',1.),'MinimumRate':('d',1.),'MaximumRate':('d',1.),'Shuffle':('b',False),'Volume':('d',1.),'Position':('x',0)}}
xml='<node>'+''.join('<interface name="'+i+'">'+''.join('<property name="'+k+'" type="'+t+'" access="read"/>' for k,(t,v) in p.items())+(''.join('<method name="'+m+'"/>' for m in ['PlayPause','Play','Pause','Next','Previous']) if i==player else '')+'</interface>' for i,p in props.items())+'</node>'
node=Gio.DBusNodeInfo.new_for_xml(xml); bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
def method(conn,sender,path,iface,method,args,invocation):
    with (root/'media-actions').open('a') as f:f.write(method+'\n')
    if method in ('PlayPause','Play','Pause'):
        status='Paused' if method=='Pause' or (method=='PlayPause' and props[player]['PlaybackStatus'][1]=='Playing') else 'Playing'
        props[player]['PlaybackStatus']=('s',status)
        bus.emit_signal(None,'/org/mpris/MediaPlayer2','org.freedesktop.DBus.Properties','PropertiesChanged',GLib.Variant('(sa{sv}as)',(player,{'PlaybackStatus':GLib.Variant('s',status)},[])))
    invocation.return_value(GLib.Variant('()',()))
def get(conn,sender,path,iface,prop):
    return GLib.Variant(*props[iface][prop])
for interface in node.interfaces:bus.register_object('/org/mpris/MediaPlayer2',interface,method,get,None)
Gio.bus_own_name_on_connection(bus,name,Gio.BusNameOwnerFlags.NONE,lambda *_:(root/'media-ready').write_text('ready'),None)
GLib.MainLoop().run()
