#!/usr/bin/env python3
"""AnatomyDPS - Project Gorgon Damage Parser with real-time log monitoring."""

import os, re, threading, time, json, queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple, Set

try:
    import pandas as pd
except ImportError:
    print("ERROR: pandas is required. Install with: pip install pandas")
    raise

from playerlog_reader import PlayerLogReader

# Configuration
DEFAULT_LOG_PATH = os.path.expandvars(r'C:\Users\%USERNAME%\AppData\LocalLow\Elder Game\Project Gorgon\Player.log')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'damage_parser.cfg')
ALIASES_PATH = os.path.join(SCRIPT_DIR, 'damage_parser_aliases.json')

# Regex patterns
TIMESTAMP_PATTERN = re.compile(r'^\[(\d{2}:\d{2}:\d{2})\]')
ZONE_PATTERNS = [
    re.compile(r'^\[(\d{2}:\d{2}:\d{2})\].*LOADING LEVEL (Area\w+)'),
    re.compile(r'^\[(\d{2}:\d{2}:\d{2})\].*Initializing area!.*:\s*(Area\w+)'),
    re.compile(r'^\[(\d{2}:\d{2}:\d{2})\].*C_INIT2 for (Area\w+)')
]
CHARACTER_PATTERN = re.compile(r'Vivox - LoginAsync\((\w+)\)')
# New format: ProcessTalkScreen(npc_id, "Search Corpse of NPC_NAME", "...Detailed Analysis:\nPlayer: N health dmg...", ...)
CORPSE_PATTERN = re.compile(r'ProcessTalkScreen\((\d+),\s*"Search Corpse of ([^"]+)",\s*"([^"]*)"')
# Damage entry pattern for lines within the detailed analysis section
DAMAGE_PATTERN = re.compile(r'^([^:]+):\s*(?:(\d+)\s+health\s+dmg)?\s*(?:(\d+)\s+armor\s+dmg)?(?:.*?Aggro\s*\(at death\):\s*([\d.]+)%)?')
WISDOM_PATTERN = re.compile(r'You earned (\d+) Combat Wisdom')

SKIP_ZONES = frozenset(['ChooseCharacter', 'ReconnectToServer', 'LoadingScene'])
BATCH_SIZE = 1000
TIMEZONE_OPTIONS = {'UTC': 0, 'EST (UTC-5)': -5, 'EDT (UTC-4)': -4, 'CST (UTC-6)': -6,
                    'CDT (UTC-5)': -5, 'MST (UTC-7)': -7, 'MDT (UTC-6)': -6, 'PST (UTC-8)': -8, 'PDT (UTC-7)': -7}

# Utility functions
def format_damage_short(value: int) -> str:
    if value >= 1_000_000: return f"{value/1_000_000:.1f}M"
    elif value >= 1_000: return f"{value/1_000:.1f}K"
    return str(value)

def group_damage_by_alias(data: List[Dict]) -> List[Dict]:
    if not data: return []
    grouped = {}
    for d in data:
        key = d['display_name']
        if key not in grouped:
            grouped[key] = {'display_name': key, 'health_dmg': 0, 'armor_dmg': 0, 'total_dmg': 0,
                           'weighted_aggro': 0, 'kills': 0, 'first_hit': None, 'last_hit': None}
        g = grouped[key]
        g['health_dmg'] += d['health_dmg']; g['armor_dmg'] += d['armor_dmg']
        g['total_dmg'] += d['total_dmg']; g['weighted_aggro'] += d.get('weighted_aggro', 0); g['kills'] += d['kills']
        if d['first_hit'] and (g['first_hit'] is None or d['first_hit'] < g['first_hit']): g['first_hit'] = d['first_hit']
        if d['last_hit'] and (g['last_hit'] is None or d['last_hit'] > g['last_hit']): g['last_hit'] = d['last_hit']
    return sorted(grouped.values(), key=lambda x: x['total_dmg'], reverse=True)

def load_config() -> Dict:
    config = {'timezone': 'EST (UTC-5)'}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                for line in f:
                    if '=' in line: k, v = line.strip().split('=', 1); config[k] = v
        except: pass
    return config

def save_config(config: Dict):
    try:
        with open(CONFIG_PATH, 'w') as f:
            for k, v in config.items(): f.write(f"{k}={v}\n")
    except: pass

def load_aliases() -> Dict[str, str]:
    if os.path.exists(ALIASES_PATH):
        try:
            with open(ALIASES_PATH, 'r') as f: return json.load(f)
        except: pass
    return {}

def save_aliases(aliases: Dict[str, str]):
    try:
        with open(ALIASES_PATH, 'w') as f: json.dump(aliases, f, indent=2)
    except: pass

def make_treeview_sortable(tree: ttk.Treeview, preserve_selection: bool = False):
    def sort_column(col, reverse):
        saved = tree.selection() if preserve_selection else ()
        items = [(tree.set(k, col), k) for k in tree.get_children('')]
        def parse_val(v):
            v = v.replace(',', '').replace('%', '').replace('★', '').strip()
            if v in ('--', '(current)', '', '(no alias)'): return float('-inf') if not reverse else float('inf')
            try: return float(v)
            except: return v.lower() if isinstance(v, str) else v
        try: items.sort(key=lambda t: parse_val(t[0]), reverse=reverse)
        except TypeError: items.sort(key=lambda t: str(t[0]), reverse=reverse)
        for i, (_, k) in enumerate(items): tree.move(k, '', i)
        if preserve_selection:
            for item in saved:
                if tree.exists(item): tree.selection_add(item)
        tree.heading(col, command=lambda: sort_column(col, not reverse))
    for col in tree['columns']: tree.heading(col, command=lambda c=col: sort_column(c, False))

def create_treeview(parent, columns, widths=None, height=20):
    tree = ttk.Treeview(parent, columns=columns, show='headings', height=height)
    for i, col in enumerate(columns):
        tree.heading(col, text=col)
        w = widths[i] if widths and i < len(widths) else 100
        tree.column(col, width=w, anchor='w' if i == 0 else 'center')
    make_treeview_sortable(tree)
    return tree

@dataclass
class DamageEvent:
    player_name: str; health_dmg: int; armor_dmg: int; aggro_percent: float
    npc_id: int; npc_name: str; zone_name: str; timestamp: datetime
    character_name: str; zone_id: Optional[int] = None

@dataclass
class ZoneInfo:
    name: str; entered_time: datetime; character_name: str

class PandasDataStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._players_list, self._zones_list, self._events_list, self._wisdom_list = [], [], [], []
        self._players_df_cache = self._zones_df_cache = self._events_df_cache = None
        self._player_name_to_id, self._player_id_to_info = {}, {}
        self._zone_id_to_info, self._zone_key_to_id = {}, {}
        self._aliases = load_aliases()
        self._next_player_id = self._next_zone_id = self._next_event_id = 1

    def _invalidate_cache(self, which='all'):
        if which in ('all', 'players'): self._players_df_cache = None
        if which in ('all', 'zones'): self._zones_df_cache = None
        if which in ('all', 'events'): self._events_df_cache = None

    def _get_events_df(self) -> pd.DataFrame:
        if self._events_df_cache is None:
            if self._events_list:
                self._events_df_cache = pd.DataFrame(self._events_list)
                if 'timestamp' in self._events_df_cache.columns:
                    self._events_df_cache['timestamp'] = pd.to_datetime(self._events_df_cache['timestamp'])
            else:
                self._events_df_cache = pd.DataFrame(columns=['event_id','zone_id','npc_id','npc_name','player_id','health_dmg','armor_dmg','aggro_percent','timestamp','character_name'])
        return self._events_df_cache

    def get_or_create_player(self, name: str) -> int:
        with self._lock:
            if name in self._player_name_to_id: return self._player_name_to_id[name]
            pid = self._next_player_id; self._next_player_id += 1
            alias = self._aliases.get(name)
            self._players_list.append({'player_id': pid, 'original_name': name, 'alias': alias})
            self._player_name_to_id[name] = pid
            self._player_id_to_info[pid] = {'name': name, 'alias': alias}
            self._invalidate_cache('players')
            return pid

    def get_or_create_players_batch(self, names: Set[str]) -> Dict[str, int]:
        result = {}
        with self._lock:
            for name in names:
                if name in self._player_name_to_id: result[name] = self._player_name_to_id[name]
                else:
                    pid = self._next_player_id; self._next_player_id += 1
                    alias = self._aliases.get(name)
                    self._players_list.append({'player_id': pid, 'original_name': name, 'alias': alias})
                    self._player_name_to_id[name] = pid
                    self._player_id_to_info[pid] = {'name': name, 'alias': alias}
                    result[name] = pid
            if result: self._invalidate_cache('players')
        return result

    def update_player_alias(self, player_id: int, alias: str):
        with self._lock:
            if player_id not in self._player_id_to_info: return
            info = self._player_id_to_info[player_id]
            info['alias'] = alias if alias else None
            for p in self._players_list:
                if p['player_id'] == player_id: p['alias'] = alias if alias else None; break
            if alias: self._aliases[info['name']] = alias
            elif info['name'] in self._aliases: del self._aliases[info['name']]
            save_aliases(self._aliases)
            self._invalidate_cache('players')

    def get_all_players(self, filter_text: str = None) -> List[Tuple[int, str, str]]:
        with self._lock:
            if not self._events_list: return []
            events_df = self._get_events_df()
            mask = (events_df['health_dmg'] > 0) | (events_df['armor_dmg'] > 0)
            active_ids = set(events_df.loc[mask, 'player_id'].unique())
            results = []
            for pid in active_ids:
                if pid in self._player_id_to_info:
                    info = self._player_id_to_info[pid]
                    if filter_text and filter_text.lower() not in info['name'].lower(): continue
                    results.append((pid, info['name'], info['alias']))
            return sorted(results, key=lambda x: x[1])

    def _get_display_name(self, pid: int) -> str:
        info = self._player_id_to_info.get(pid)
        return (info['alias'] or info['name']) if info else "Unknown"

    def create_zone_entry(self, name: str, char_name: str, entered_time: datetime, log_date: str = None) -> Optional[int]:
        with self._lock:
            open_zone = None
            for z in reversed(self._zones_list):
                if z['character_name'] == char_name and z['left_time'] is None: open_zone = z; break
            if open_zone and open_zone['name'] == name: return open_zone['zone_id']
            if open_zone: open_zone['left_time'] = entered_time
            zid = self._next_zone_id; self._next_zone_id += 1
            zone_dict = {'zone_id': zid, 'name': name, 'character_name': char_name,
                        'entered_time': entered_time, 'left_time': None, 'log_date': log_date}
            self._zones_list.append(zone_dict)
            self._zone_id_to_info[zid] = zone_dict
            self._invalidate_cache('zones')
            return zid

    def get_current_zone_id(self, char_name: str) -> Optional[int]:
        with self._lock:
            for z in reversed(self._zones_list):
                if z['character_name'] == char_name and z['left_time'] is None: return z['zone_id']
            return None

    def insert_damage_event(self, event: DamageEvent, zone_id: int) -> int:
        if event.health_dmg == 0 and event.armor_dmg == 0: return -1
        pid = self.get_or_create_player(event.player_name)
        with self._lock:
            eid = self._next_event_id; self._next_event_id += 1
            self._events_list.append({'event_id': eid, 'zone_id': zone_id, 'npc_id': event.npc_id,
                'npc_name': event.npc_name, 'player_id': pid, 'health_dmg': event.health_dmg,
                'armor_dmg': event.armor_dmg, 'aggro_percent': event.aggro_percent,
                'timestamp': event.timestamp, 'character_name': event.character_name})
            self._invalidate_cache('events')
            return eid

    def insert_damage_events_batch(self, events: List[Tuple]) -> int:
        if not events: return 0
        with self._lock:
            for e in events:
                zid, npc_id, npc_name, pid, h_dmg, a_dmg, aggro, ts, char = e
                eid = self._next_event_id; self._next_event_id += 1
                self._events_list.append({'event_id': eid, 'zone_id': zid, 'npc_id': npc_id,
                    'npc_name': npc_name, 'player_id': pid, 'health_dmg': h_dmg, 'armor_dmg': a_dmg,
                    'aggro_percent': aggro, 'timestamp': ts, 'character_name': char})
            self._invalidate_cache('events')
        return len(events)

    def add_wisdom(self, zone_id: int, amount: int):
        with self._lock: self._wisdom_list.append({'zone_id': zone_id, 'amount': amount})

    def get_damage_by_zones(self, zone_ids: List[int]) -> List[Dict]:
        with self._lock:
            events_df = self._get_events_df()
            if events_df.empty: return []
            zone_events = events_df[events_df['zone_id'].isin(zone_ids)]
            if zone_events.empty: return []
            # Calculate total damage per NPC for weighted aggro
            npc_totals = zone_events.groupby(['zone_id', 'npc_id']).agg({'health_dmg': 'sum', 'armor_dmg': 'sum'}).reset_index()
            npc_totals['npc_total'] = npc_totals['health_dmg'] + npc_totals['armor_dmg']
            npc_total_map = {(r['zone_id'], r['npc_id']): r['npc_total'] for _, r in npc_totals.iterrows()}
            results = []
            for pid, grp in zone_events.groupby('player_id'):
                # Calculate weighted aggro: sum of (aggro% * npc_total_damage) for each event
                weighted_aggro = 0.0
                for _, row in grp.iterrows():
                    npc_total = npc_total_map.get((row['zone_id'], row['npc_id']), 0)
                    weighted_aggro += (row['aggro_percent'] / 100.0) * npc_total
                results.append({'player_id': pid, 'display_name': self._get_display_name(pid),
                    'health_dmg': int(grp['health_dmg'].sum()), 'armor_dmg': int(grp['armor_dmg'].sum()),
                    'total_dmg': int(grp['health_dmg'].sum() + grp['armor_dmg'].sum()),
                    'weighted_aggro': int(weighted_aggro),
                    'kills': len(grp['npc_id'].unique()), 'first_hit': grp['timestamp'].min(), 'last_hit': grp['timestamp'].max()})
            return sorted(results, key=lambda x: x['total_dmg'], reverse=True)

    def get_damage_in_time_range(self, start: datetime, end: datetime) -> List[Dict]:
        with self._lock:
            events_df = self._get_events_df()
            if events_df.empty: return []
            mask = (events_df['timestamp'] >= start) & (events_df['timestamp'] <= end)
            range_events = events_df[mask]
            if range_events.empty: return []
            # Calculate total damage per NPC for weighted aggro
            npc_totals = range_events.groupby(['zone_id', 'npc_id']).agg({'health_dmg': 'sum', 'armor_dmg': 'sum'}).reset_index()
            npc_totals['npc_total'] = npc_totals['health_dmg'] + npc_totals['armor_dmg']
            npc_total_map = {(r['zone_id'], r['npc_id']): r['npc_total'] for _, r in npc_totals.iterrows()}
            results = []
            for pid, grp in range_events.groupby('player_id'):
                weighted_aggro = 0.0
                for _, row in grp.iterrows():
                    npc_total = npc_total_map.get((row['zone_id'], row['npc_id']), 0)
                    weighted_aggro += (row['aggro_percent'] / 100.0) * npc_total
                results.append({'player_id': pid, 'display_name': self._get_display_name(pid),
                    'health_dmg': int(grp['health_dmg'].sum()), 'armor_dmg': int(grp['armor_dmg'].sum()),
                    'total_dmg': int(grp['health_dmg'].sum() + grp['armor_dmg'].sum()),
                    'weighted_aggro': int(weighted_aggro),
                    'kills': len(grp['npc_id'].unique()), 'first_hit': grp['timestamp'].min(), 'last_hit': grp['timestamp'].max()})
            return sorted(results, key=lambda x: x['total_dmg'], reverse=True)

    def get_zones_combat_times(self, zone_ids: List[int]) -> Tuple[Optional[datetime], Optional[datetime], int]:
        with self._lock:
            events_df = self._get_events_df()
            if events_df.empty: return None, None, 0
            zone_events = events_df[events_df['zone_id'].isin(zone_ids)]
            if zone_events.empty: return None, None, 0
            kills = len(zone_events.groupby(['zone_id', 'npc_id']))
            return zone_events['timestamp'].min(), zone_events['timestamp'].max(), kills

    def get_all_zone_instances(self, zone_name: str = None, log_date: str = None) -> List[Dict]:
        with self._lock:
            results = []
            for z in self._zones_list:
                if zone_name and z['name'] != zone_name: continue
                if log_date and z.get('log_date') != log_date: continue
                results.append(z.copy())
            return sorted(results, key=lambda x: x['entered_time'], reverse=True)

    def get_zone_stats(self, zone_id: int) -> Dict:
        with self._lock:
            wisdom = sum(w['amount'] for w in self._wisdom_list if w['zone_id'] == zone_id)
            events_df = self._get_events_df()
            if events_df.empty: return {'wisdom': wisdom, 'kills': 0, 'total_dmg': 0}
            zone_events = events_df[events_df['zone_id'] == zone_id]
            if zone_events.empty: return {'wisdom': wisdom, 'kills': 0, 'total_dmg': 0}
            return {'wisdom': wisdom, 'kills': len(zone_events['npc_id'].unique()),
                    'total_dmg': int(zone_events['health_dmg'].sum() + zone_events['armor_dmg'].sum())}

    def get_unique_zone_names(self) -> List[str]:
        with self._lock: return sorted(set(z['name'] for z in self._zones_list))

    def get_unique_log_dates(self) -> List[str]:
        with self._lock: return sorted((z.get('log_date') for z in self._zones_list if z.get('log_date')), reverse=True)

    def get_latest_damage_timestamp(self) -> Optional[datetime]:
        with self._lock:
            events_df = self._get_events_df()
            return events_df['timestamp'].max() if not events_df.empty else None

    def get_all_existing_event_keys(self, log_date: str = None) -> Set[Tuple]:
        with self._lock:
            events_df = self._get_events_df()
            if events_df.empty: return set()
            return {(r['zone_id'], r['npc_id'], r['player_id'], r['health_dmg'], r['armor_dmg']) for _, r in events_df.iterrows()}

    def clear_all_data(self):
        with self._lock:
            self._players_list.clear(); self._zones_list.clear(); self._events_list.clear(); self._wisdom_list.clear()
            self._invalidate_cache()
            self._player_name_to_id.clear(); self._player_id_to_info.clear()
            self._zone_id_to_info.clear(); self._zone_key_to_id.clear()
            self._next_player_id = self._next_zone_id = self._next_event_id = 1

    def get_stats(self) -> Dict:
        with self._lock: return {'zones': len(self._zones_list), 'events': len(self._events_list), 'players': len(self._players_list)}

    def get_monster_summary_by_zones(self, zone_ids: List[int], filter_text: str = None) -> List[Dict]:
        """Get aggregated damage by monster name across selected zones."""
        with self._lock:
            events_df = self._get_events_df()
            if events_df.empty: return []
            zone_events = events_df[events_df['zone_id'].isin(zone_ids)]
            if zone_events.empty: return []
            
            # Group by npc_name and aggregate
            results = []
            for npc_name, grp in zone_events.groupby('npc_name'):
                if filter_text and filter_text.lower() not in npc_name.lower():
                    continue
                kill_count = len(grp.groupby(['zone_id', 'npc_id']))  # Unique kills
                results.append({
                    'npc_name': npc_name,
                    'health_dmg': int(grp['health_dmg'].sum()),
                    'armor_dmg': int(grp['armor_dmg'].sum()),
                    'total_dmg': int(grp['health_dmg'].sum() + grp['armor_dmg'].sum()),
                    'kill_count': kill_count,
                    'first_kill': grp['timestamp'].min(),
                    'last_kill': grp['timestamp'].max()
                })
            return sorted(results, key=lambda x: x['total_dmg'], reverse=True)

    def get_monster_player_summary(self, zone_ids: List[int], npc_name: str) -> List[Dict]:
        """Get aggregated player damage for a specific monster type across zones, grouped by alias."""
        with self._lock:
            events_df = self._get_events_df()
            if events_df.empty: return []
            zone_events = events_df[(events_df['zone_id'].isin(zone_ids)) & (events_df['npc_name'] == npc_name)]
            if zone_events.empty: return []
            
            # Calculate total damage per NPC for weighted aggro
            npc_totals = zone_events.groupby(['zone_id', 'npc_id']).agg({'health_dmg': 'sum', 'armor_dmg': 'sum'}).reset_index()
            npc_totals['npc_total'] = npc_totals['health_dmg'] + npc_totals['armor_dmg']
            npc_total_map = {(r['zone_id'], r['npc_id']): r['npc_total'] for _, r in npc_totals.iterrows()}
            
            # First pass: group by player_id
            player_data = []
            for pid, grp in zone_events.groupby('player_id'):
                weighted_aggro = 0.0
                for _, row in grp.iterrows():
                    npc_total = npc_total_map.get((row['zone_id'], row['npc_id']), 0)
                    weighted_aggro += (row['aggro_percent'] / 100.0) * npc_total
                
                kills = len(grp.groupby(['zone_id', 'npc_id']))
                player_data.append({
                    'display_name': self._get_display_name(pid),
                    'health_dmg': int(grp['health_dmg'].sum()),
                    'armor_dmg': int(grp['armor_dmg'].sum()),
                    'total_dmg': int(grp['health_dmg'].sum() + grp['armor_dmg'].sum()),
                    'weighted_aggro': int(weighted_aggro),
                    'kills': kills
                })
            
            # Second pass: group by display_name (alias) to combine players with same alias
            grouped = {}
            for d in player_data:
                key = d['display_name']
                if key not in grouped:
                    grouped[key] = {'display_name': key, 'health_dmg': 0, 'armor_dmg': 0, 'total_dmg': 0,
                                   'weighted_aggro': 0, 'kills': 0}
                g = grouped[key]
                g['health_dmg'] += d['health_dmg']
                g['armor_dmg'] += d['armor_dmg']
                g['total_dmg'] += d['total_dmg']
                g['weighted_aggro'] += d['weighted_aggro']
                g['kills'] += d['kills']
            
            return sorted(grouped.values(), key=lambda x: x['total_dmg'], reverse=True)

    def get_monster_kill_details(self, zone_ids: List[int], npc_name: str) -> List[Dict]:
        """Get details for each kill of a specific monster type across zones."""
        with self._lock:
            events_df = self._get_events_df()
            if events_df.empty: return []
            zone_events = events_df[(events_df['zone_id'].isin(zone_ids)) & (events_df['npc_name'] == npc_name)]
            if zone_events.empty: return []
            
            # Calculate total damage per NPC for weighted aggro
            npc_totals = zone_events.groupby(['zone_id', 'npc_id']).agg({'health_dmg': 'sum', 'armor_dmg': 'sum'}).reset_index()
            npc_totals['npc_total'] = npc_totals['health_dmg'] + npc_totals['armor_dmg']
            npc_total_map = {(r['zone_id'], r['npc_id']): r['npc_total'] for _, r in npc_totals.iterrows()}
            
            # Group by zone_id and npc_id to get individual kills
            results = []
            for (zone_id, npc_id), kill_grp in zone_events.groupby(['zone_id', 'npc_id']):
                zone_name = self._zone_id_to_info.get(zone_id, {}).get('name', 'Unknown')
                total_dmg = int(kill_grp['health_dmg'].sum() + kill_grp['armor_dmg'].sum())
                kill_time = kill_grp['timestamp'].max()
                npc_total = npc_total_map.get((zone_id, npc_id), total_dmg)
                
                # Get player breakdown for this kill
                player_dmg = []
                for pid, player_grp in kill_grp.groupby('player_id'):
                    weighted_aggro = (player_grp['aggro_percent'].max() / 100.0) * npc_total
                    player_dmg.append({
                        'display_name': self._get_display_name(pid),
                        'health_dmg': int(player_grp['health_dmg'].sum()),
                        'armor_dmg': int(player_grp['armor_dmg'].sum()),
                        'total_dmg': int(player_grp['health_dmg'].sum() + player_grp['armor_dmg'].sum()),
                        'weighted_aggro': int(weighted_aggro),
                        'aggro_percent': float(player_grp['aggro_percent'].max())
                    })
                player_dmg.sort(key=lambda x: x['total_dmg'], reverse=True)
                
                results.append({
                    'zone_id': zone_id,
                    'npc_id': npc_id,
                    'zone_name': zone_name,
                    'total_dmg': total_dmg,
                    'kill_time': kill_time,
                    'players': player_dmg
                })
            return sorted(results, key=lambda x: x['kill_time'], reverse=True)


class LogParser:
    def __init__(self, data_store: PandasDataStore, event_queue: queue.Queue):
        self.data_store = data_store
        self.event_queue = event_queue
        self.current_character = self.current_zone = self.current_zone_id = None
        self.current_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.last_timestamp = self.log_date = None
        self.batch_mode = False
        self.pending_events, self.seen_events, self.zones_created = [], set(), {}
        self.last_zone_name = self.last_zone_time = None
        self.zone_debounce_seconds = 30

    def set_log_date(self, date_str: str):
        self.log_date = date_str
        try: self.current_date = datetime.strptime(date_str, '%Y-%m-%d')
        except: pass

    def reset(self):
        self.current_character = self.current_zone = self.current_zone_id = None
        self.last_timestamp = None
        self.pending_events.clear(); self.seen_events.clear(); self.zones_created.clear()
        self.last_zone_name = self.last_zone_time = None

    def start_batch_mode(self, existing_events: Set[Tuple] = None):
        self.batch_mode = True; self.pending_events.clear()
        self.seen_events = existing_events or set(); self.zones_created.clear()

    def end_batch_mode(self) -> int:
        self.batch_mode = False; count = self._flush_batch(); self.pending_events.clear(); return count

    def _flush_batch(self) -> int:
        if not self.pending_events: return 0
        player_ids = self.data_store.get_or_create_players_batch({e.player_name for e in self.pending_events})
        events_to_insert = []
        for e in self.pending_events:
            pid = player_ids.get(e.player_name)
            if not pid: continue
            zid = e.zone_id
            if not zid:
                zone_key = (e.zone_name, e.character_name)
                zid = self.zones_created.get(zone_key) or self.data_store.create_zone_entry(e.zone_name, e.character_name, e.timestamp, self.log_date)
                self.zones_created[zone_key] = zid
            if not zid: continue
            dedup_key = (zid, e.npc_id, pid, e.health_dmg, e.armor_dmg)
            if dedup_key in self.seen_events: continue
            self.seen_events.add(dedup_key)
            events_to_insert.append((zid, e.npc_id, e.npc_name, pid, e.health_dmg, e.armor_dmg, e.aggro_percent, e.timestamp, e.character_name))
        count = self.data_store.insert_damage_events_batch(events_to_insert)
        self.pending_events.clear()
        return count

    def parse_timestamp(self, time_str: str) -> datetime:
        time_obj = datetime.strptime(time_str, '%H:%M:%S').time()
        result = datetime.combine(self.current_date.date(), time_obj)
        if self.last_timestamp and result < self.last_timestamp:
            self.current_date += timedelta(days=1)
            result = datetime.combine(self.current_date.date(), time_obj)
        self.last_timestamp = result
        return result

    def parse_line(self, line: str) -> Optional[DamageEvent]:
        line = line.strip()
        if not line: return None

        char_match = CHARACTER_PATTERN.search(line)
        if char_match:
            self.current_character = char_match.group(1)
            if not self.batch_mode: self.event_queue.put(('character', self.current_character))
            return None

        for pattern in ZONE_PATTERNS:
            m = pattern.search(line)
            if m:
                time_str, zone_name = m.groups()
                if zone_name in SKIP_ZONES: continue
                ts = self.parse_timestamp(time_str)
                if self.last_zone_name == zone_name and self.last_zone_time and (ts - self.last_zone_time).total_seconds() < self.zone_debounce_seconds:
                    self.current_zone = ZoneInfo(zone_name, ts, self.current_character); return None
                self.current_zone = ZoneInfo(zone_name, ts, self.current_character)
                self.last_zone_name, self.last_zone_time = zone_name, ts
                self.current_zone_id = self.data_store.create_zone_entry(zone_name, self.current_character, ts, self.log_date)
                if self.batch_mode: self.zones_created[(zone_name, self.current_character)] = self.current_zone_id
                else: self.event_queue.put(('zone', zone_name, ts))
                return None

        wisdom_match = WISDOM_PATTERN.search(line)
        if wisdom_match and self.current_zone_id:
            self.data_store.add_wisdom(self.current_zone_id, int(wisdom_match.group(1))); return None

        # New format: entire corpse data is on single line
        corpse_match = CORPSE_PATTERN.search(line)
        if corpse_match:
            ts_match = TIMESTAMP_PATTERN.match(line)
            ts = self.parse_timestamp(ts_match.group(1)) if ts_match else datetime.now()
            npc_id = int(corpse_match.group(1))
            npc_name = corpse_match.group(2).strip()
            content = corpse_match.group(3)
            
            # Check if there's actual damage data (look for "Detailed Analysis")
            if '<h2>Detailed Analysis:</h2>' not in content:
                return None
            
            # Extract the damage section after "Detailed Analysis"
            analysis_start = content.find('<h2>Detailed Analysis:</h2>')
            if analysis_start == -1:
                return None
            damage_section = content[analysis_start + len('<h2>Detailed Analysis:</h2>'):]
            
            # Split by literal \n (backslash-n in the log file)
            # In Python string literals, '\\n' represents the two-character sequence backslash + n
            damage_lines = damage_section.split('\\n')
            
            last_event = None
            for damage_line in damage_lines:
                damage_line = damage_line.strip()
                if not damage_line:
                    continue
                    
                damage_match = DAMAGE_PATTERN.match(damage_line)
                if damage_match:
                    player_name = damage_match.group(1).strip()
                    h_dmg = int(damage_match.group(2)) if damage_match.group(2) else 0
                    a_dmg = int(damage_match.group(3)) if damage_match.group(3) else 0
                    
                    # Skip entries with no damage
                    if h_dmg == 0 and a_dmg == 0:
                        continue
                        
                    aggro = float(damage_match.group(4)) if damage_match.group(4) else 0.0
                    
                    event = DamageEvent(player_name, h_dmg, a_dmg, aggro, npc_id, npc_name,
                                       self.current_zone.name if self.current_zone else "Unknown", ts,
                                       self.current_character, self.current_zone_id)
                    
                    if self.batch_mode:
                        self.pending_events.append(event)
                        if len(self.pending_events) >= BATCH_SIZE: self._flush_batch()
                    else:
                        if self.data_store.insert_damage_event(event, self.current_zone_id) != -1:
                            self.event_queue.put(('damage', event))
                    
                    last_event = event
            
            return last_event
        
        return None


class LogMonitor:
    def __init__(self, log_path: str, parser: LogParser):
        self.log_path, self.parser = log_path, parser
        self.running = False; self.thread = None
        self.reader = PlayerLogReader(log_path)

    def start(self, from_position: int = 0):
        if self.running: return
        self.running = True; self.reader.set_position(from_position)
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True); self.thread.start()

    def stop(self):
        self.running = False
        if self.thread: self.thread.join(timeout=2)

    def _monitor_loop(self):
        try:
            while self.running:
                for line in self.reader.read_new_lines():
                    if not self.running: break
                    try: self.parser.parse_line(line)
                    except: pass
                if self.running: time.sleep(0.1)
        except Exception as e: self.parser.event_queue.put(('error', str(e)))


class BackgroundLoader:
    def __init__(self, data_store: PandasDataStore, progress_cb=None, complete_cb=None):
        self.data_store, self.progress_cb, self.complete_cb = data_store, progress_cb, complete_cb
        self.thread = None; self.cancel_requested = False

    def load_file(self, log_path: str, log_date: str):
        self.cancel_requested = False
        self.thread = threading.Thread(target=self._load_worker, args=(log_path, log_date), daemon=True)
        self.thread.start()

    def cancel(self): self.cancel_requested = True

    def _load_worker(self, log_path: str, log_date: str):
        try:
            file_size = os.path.getsize(log_path)
            parser = LogParser(self.data_store, queue.Queue())
            parser.set_log_date(log_date)
            if self.progress_cb: self.progress_cb(0, "Checking for duplicates...")
            parser.start_batch_mode(self.data_store.get_all_existing_event_keys(log_date))
            if self.progress_cb: self.progress_cb(0, "Loading log file...")
            line_count = damage_count = last_progress = 0
            current_char = current_zone = None
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                while True:
                    if self.cancel_requested:
                        if self.complete_cb: self.complete_cb(False, "Cancelled", 0, 0, None, None)
                        return
                    line = f.readline()
                    if not line: break
                    line_count += 1
                    try:
                        if parser.parse_line(line): damage_count += 1
                        if parser.current_character != current_char: current_char = parser.current_character
                        if parser.current_zone and parser.current_zone.name != current_zone: current_zone = parser.current_zone.name
                    except: pass
                    if line_count % 10000 == 0:
                        progress = int((f.tell() / file_size) * 100)
                        if progress != last_progress and self.progress_cb:
                            self.progress_cb(progress, f"Processing... {line_count:,} lines, {damage_count:,} damage events")
                            last_progress = progress
            if self.progress_cb: self.progress_cb(95, "Finalizing...")
            damage_count += parser.end_batch_mode()
            if self.progress_cb: self.progress_cb(100, "Complete!")
            if self.complete_cb: self.complete_cb(True, f"Loaded {line_count:,} lines, {damage_count:,} damage events", line_count, damage_count, current_char, current_zone)
        except Exception as e:
            if self.complete_cb: self.complete_cb(False, f"Error: {str(e)}", 0, 0, None, None)


class DamageParserGUI:
    def __init__(self, log_path: str = None):
        self.root = tk.Tk()
        self.root.title("AnatomyDPS - Project Gorgon Damage Parser")
        self.root.geometry("1300x850")
        self.log_path = log_path or DEFAULT_LOG_PATH
        self.data_store = PandasDataStore()
        self.event_queue = queue.Queue()
        self.parser = LogParser(self.data_store, self.event_queue)
        self.monitor = self.loader = None
        self.current_character = self.current_zone = self.current_zone_id = None
        self.monitoring_active = self.loading_active = False
        self.config = load_config()
        self.timezone_var = tk.StringVar(value=self.config.get('timezone', 'EST (UTC-5)'))
        self.min_wisdom_var = tk.StringVar(value="")  # Shared between Zone Runs and Monsters tabs
        self.mini_window = None
        self._create_ui()
        self._start_event_processor()
        self._start_auto_refresh()
        self.root.after(100, self._auto_start)

    def _get_tz_offset(self) -> int: return TIMEZONE_OPTIONS.get(self.timezone_var.get(), -5)
    def _apply_tz(self, dt): return dt + timedelta(hours=self._get_tz_offset()) if dt else None
    def _format_time(self, dt): return self._apply_tz(dt).strftime('%H:%M:%S') if dt else "--"

    def _auto_start(self):
        if os.path.exists(self.log_path):
            self._add_feed_line("Auto-loading player.log (full file)...", 'character')
            self._load_file_background(self.log_path, monitor_after=True)
        else:
            self._add_feed_line(f"Player.log not found at: {self.log_path}", 'error')

    def _create_ui(self):
        # Menu
        menubar = tk.Menu(self.root); self.root.config(menu=menubar)
        file_menu = tk.Menu(menubar, tearoff=0); menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Import Log File...", command=self._import_log_file)
        file_menu.add_command(label="Export to CSV...", command=self._export_csv)
        file_menu.add_separator(); file_menu.add_command(label="Exit", command=self._on_close)
        session_menu = tk.Menu(menubar, tearoff=0); menubar.add_cascade(label="Session", menu=session_menu)
        session_menu.add_command(label="Clear All Data", command=self._clear_all_data)
        view_menu = tk.Menu(menubar, tearoff=0); menubar.add_cascade(label="View", menu=view_menu)
        tz_menu = tk.Menu(view_menu, tearoff=0); view_menu.add_cascade(label="Timezone", menu=tz_menu)
        for tz in TIMEZONE_OPTIONS: tz_menu.add_radiobutton(label=tz, variable=self.timezone_var, value=tz, command=self._on_tz_changed)

        # Control bar
        ctrl = ttk.Frame(self.root, padding="5"); ctrl.pack(fill='x')
        self.monitor_btn = ttk.Button(ctrl, text="▶ Start Monitoring", command=self._toggle_monitoring); self.monitor_btn.pack(side='left', padx=5)
        ttk.Button(ctrl, text="Import Log...", command=self._import_log_file).pack(side='left', padx=5)
        ttk.Button(ctrl, text="📊 Mini View", command=self._open_mini_window).pack(side='left', padx=5)
        self.char_label = ttk.Label(ctrl, text="Character: --"); self.char_label.pack(side='left', padx=20)
        self.zone_label = ttk.Label(ctrl, text="Zone: --"); self.zone_label.pack(side='left', padx=20)
        self.log_date_label = ttk.Label(ctrl, text="Log Date: --"); self.log_date_label.pack(side='left', padx=20)
        self.tz_label = ttk.Label(ctrl, text=f"TZ: {self.timezone_var.get()}"); self.tz_label.pack(side='left', padx=10)
        self.status_label = ttk.Label(ctrl, text="Status: Idle", foreground='red'); self.status_label.pack(side='right', padx=5)

        # Progress bar
        self.progress_frame = ttk.Frame(self.root)
        self.progress_var = tk.DoubleVar()
        ttk.Progressbar(self.progress_frame, variable=self.progress_var, maximum=100, length=400).pack(side='left', padx=5)
        self.progress_label = ttk.Label(self.progress_frame, text=""); self.progress_label.pack(side='left', padx=10)
        ttk.Button(self.progress_frame, text="Cancel", command=lambda: self.loader and self.loader.cancel()).pack(side='left', padx=5)

        # Notebook
        self.notebook = ttk.Notebook(self.root); self.notebook.pack(fill='both', expand=True, padx=5, pady=5)
        self._create_feed_tab()
        self._create_rolling_tab()
        self._create_zones_tab()
        self._create_monsters_tab()
        self._create_alias_tab()

    def _create_feed_tab(self):
        frame = ttk.Frame(self.notebook, padding="5"); self.notebook.add(frame, text="Live Feed")
        self.feed_text = tk.Text(frame, wrap='word', height=30, state='disabled', font=('Consolas', 10))
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.feed_text.yview)
        self.feed_text.configure(yscrollcommand=scroll.set)
        self.feed_text.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')
        for tag, color, bold in [('zone','#2196F3',True),('damage','#4CAF50',False),('character','#FF9800',True),('error','#F44336',False)]:
            self.feed_text.tag_configure(tag, foreground=color, font=('Consolas', 10, 'bold') if bold else None)

    def _create_rolling_tab(self):
        frame = ttk.Frame(self.notebook, padding="5"); self.notebook.add(frame, text="Last 5 Minutes")
        top = ttk.Frame(frame); top.pack(fill='x', pady=5)
        ttk.Label(top, text="Window (minutes):").pack(side='left')
        self.window_var = tk.StringVar(value="5"); ttk.Entry(top, textvariable=self.window_var, width=5).pack(side='left', padx=5)
        ttk.Button(top, text="Refresh", command=self._refresh_rolling).pack(side='left', padx=5)
        self.rolling_info = ttk.Label(top, text=""); self.rolling_info.pack(side='left', padx=20)
        self.rolling_tree = create_treeview(frame, ('Player','Health Dmg','Armor Dmg','Total Dmg','Aggro Est','DPS','% of Group','Kills'), [180,90,90,100,90,80,80,60])
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.rolling_tree.yview)
        self.rolling_tree.configure(yscrollcommand=scroll.set)
        self.rolling_tree.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')

    def _create_zones_tab(self):
        frame = ttk.Frame(self.notebook, padding="5"); self.notebook.add(frame, text="Zone Runs")
        filt = ttk.LabelFrame(frame, text="Filters", padding="5"); filt.pack(fill='x', pady=5)
        ttk.Label(filt, text="Min Wisdom:").pack(side='left')
        ttk.Entry(filt, textvariable=self.min_wisdom_var, width=8).pack(side='left', padx=5)
        ttk.Button(filt, text="Refresh", command=self._refresh_zones_and_monsters).pack(side='left', padx=10)

        paned = ttk.PanedWindow(frame, orient='vertical'); paned.pack(fill='both', expand=True)
        zones_frame = ttk.LabelFrame(paned, text="Zone Runs (Ctrl/Shift for multi-select)", padding="5"); paned.add(zones_frame, weight=1)
        self.zones_tree = ttk.Treeview(zones_frame, columns=('Zone','Character','Date','Entered','Left','Wisdom','Kills','Total Damage'), show='headings', selectmode='extended')
        for col, w in [('Zone',150),('Character',120),('Date',100),('Entered',100),('Left',100),('Wisdom',90),('Kills',90),('Total Damage',100)]:
            self.zones_tree.heading(col, text=col); self.zones_tree.column(col, width=w, anchor='w' if col in ('Zone','Character') else 'center')
        make_treeview_sortable(self.zones_tree, preserve_selection=True)
        self.zones_tree.pack(side='left', fill='both', expand=True)
        ttk.Scrollbar(zones_frame, orient='vertical', command=self.zones_tree.yview).pack(side='right', fill='y')
        self.zones_tree.bind('<<TreeviewSelect>>', lambda e: self._update_session())

        totals = ttk.LabelFrame(paned, text="Damage Totals (selected runs)", padding="5"); paned.add(totals, weight=2)
        copy_frame = ttk.Frame(totals); copy_frame.pack(fill='x', pady=2)
        ttk.Button(copy_frame, text="Copy Full", command=lambda: self._copy_zones(False)).pack(side='left', padx=2)
        ttk.Button(copy_frame, text="Copy Compact", command=lambda: self._copy_zones(True)).pack(side='left', padx=2)
        self.selected_label = ttk.Label(totals, text="No zones selected"); self.selected_label.pack(fill='x', pady=2)
        self.session_tree = create_treeview(totals, ('Player','Health Dmg','Armor Dmg','Total Dmg','Aggro Est','DPS','%','Kills'), [180,90,90,100,90,80,60,60], height=10)
        scroll = ttk.Scrollbar(totals, orient='vertical', command=self.session_tree.yview)
        self.session_tree.configure(yscrollcommand=scroll.set)
        self.session_tree.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')

    def _create_alias_tab(self):
        frame = ttk.Frame(self.notebook, padding="5"); self.notebook.add(frame, text="Player Aliases")
        top = ttk.Frame(frame); top.pack(fill='x', pady=5)
        ttk.Label(top, text="Filter by name:").pack(side='left')
        self.alias_filter_var = tk.StringVar()
        self.alias_filter_var.trace_add('write', lambda *a: self._refresh_aliases())
        ttk.Entry(top, textvariable=self.alias_filter_var, width=30).pack(side='left', padx=5)
        ttk.Button(top, text="Refresh", command=self._refresh_aliases).pack(side='left', padx=10)
        ttk.Label(top, text="(Double-click to edit. Same alias = grouped damage.)").pack(side='left', padx=20)
        self.alias_tree = create_treeview(frame, ('Original Name','Alias'), [250,250])
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.alias_tree.yview)
        self.alias_tree.configure(yscrollcommand=scroll.set)
        self.alias_tree.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')
        self.alias_tree.bind('<Double-1>', self._edit_alias)

    def _create_monsters_tab(self):
        frame = ttk.Frame(self.notebook, padding="5"); self.notebook.add(frame, text="Monsters")
        
        # Filter section - uses shared min_wisdom_var
        filt = ttk.LabelFrame(frame, text="Filters", padding="5"); filt.pack(fill='x', pady=5)
        ttk.Label(filt, text="Min Wisdom:").pack(side='left')
        ttk.Entry(filt, textvariable=self.min_wisdom_var, width=8).pack(side='left', padx=5)
        ttk.Button(filt, text="Refresh", command=self._refresh_zones_and_monsters).pack(side='left', padx=10)
        
        paned = ttk.PanedWindow(frame, orient='vertical'); paned.pack(fill='both', expand=True)
        
        # Zone selection (same as zones tab)
        zones_frame = ttk.LabelFrame(paned, text="Zone Runs (Ctrl/Shift for multi-select)", padding="5"); paned.add(zones_frame, weight=1)
        self.monster_zones_tree = ttk.Treeview(zones_frame, columns=('Zone','Character','Date','Entered','Left','Wisdom','Kills','Total Damage'), show='headings', selectmode='extended')
        for col, w in [('Zone',150),('Character',120),('Date',100),('Entered',100),('Left',100),('Wisdom',90),('Kills',90),('Total Damage',100)]:
            self.monster_zones_tree.heading(col, text=col); self.monster_zones_tree.column(col, width=w, anchor='w' if col in ('Zone','Character') else 'center')
        make_treeview_sortable(self.monster_zones_tree, preserve_selection=True)
        self.monster_zones_tree.pack(side='left', fill='both', expand=True)
        ttk.Scrollbar(zones_frame, orient='vertical', command=self.monster_zones_tree.yview).pack(side='right', fill='y')
        self.monster_zones_tree.bind('<<TreeviewSelect>>', lambda e: self._update_monster_list())
        
        # Monster list with filter
        monsters_frame = ttk.LabelFrame(paned, text="Monsters (expand to see player damage breakdown)", padding="5"); paned.add(monsters_frame, weight=2)
        
        # Monster filter row
        monster_filter_row = ttk.Frame(monsters_frame); monster_filter_row.pack(fill='x', pady=2)
        ttk.Label(monster_filter_row, text="Filter monsters:").pack(side='left')
        self.monster_name_filter_var = tk.StringVar()
        self.monster_name_filter_var.trace_add('write', lambda *a: self._update_monster_list())
        ttk.Entry(monster_filter_row, textvariable=self.monster_name_filter_var, width=30).pack(side='left', padx=5)
        self.monster_summary_label = ttk.Label(monster_filter_row, text="No zones selected")
        self.monster_summary_label.pack(side='left', padx=20)
        
        # Monster tree - hierarchical with ability to expand
        monster_cols = ('Name','Health Dmg','Armor Dmg','Total Dmg','Aggro Est','Kills')
        self.monster_tree = ttk.Treeview(monsters_frame, columns=monster_cols, show='tree headings', height=15)
        self.monster_tree.heading('#0', text='')
        self.monster_tree.column('#0', width=30, stretch=False)
        for col, w, anchor in [('Name',220,'w'),('Health Dmg',90,'center'),('Armor Dmg',90,'center'),('Total Dmg',100,'center'),('Aggro Est',90,'center'),('Kills',60,'center')]:
            self.monster_tree.heading(col, text=col)
            self.monster_tree.column(col, width=w, anchor=anchor)
        
        # Make monster tree sortable (top-level only)
        self._make_monster_tree_sortable()
        
        monster_scroll = ttk.Scrollbar(monsters_frame, orient='vertical', command=self.monster_tree.yview)
        self.monster_tree.configure(yscrollcommand=monster_scroll.set)
        self.monster_tree.pack(side='left', fill='both', expand=True)
        monster_scroll.pack(side='right', fill='y')
        
        # Bind expand/collapse to load details
        self.monster_tree.bind('<<TreeviewOpen>>', self._on_monster_expand)

    def _make_monster_tree_sortable(self):
        """Make monster tree sortable by column headers (top-level items only)."""
        def sort_column(col, reverse):
            # Get only top-level items
            items = [(self.monster_tree.set(k, col), k) for k in self.monster_tree.get_children('')]
            def parse_val(v):
                v = v.replace(',', '').replace('%', '').strip()
                if v in ('--', '', '(no data)'):
                    return float('-inf') if not reverse else float('inf')
                try:
                    return float(v)
                except:
                    return v.lower() if isinstance(v, str) else v
            try:
                items.sort(key=lambda t: parse_val(t[0]), reverse=reverse)
            except TypeError:
                items.sort(key=lambda t: str(t[0]), reverse=reverse)
            for i, (_, k) in enumerate(items):
                self.monster_tree.move(k, '', i)
            self.monster_tree.heading(col, command=lambda: sort_column(col, not reverse))
        
        for col in self.monster_tree['columns']:
            self.monster_tree.heading(col, command=lambda c=col: sort_column(c, False))

    def _show_progress(self, show):
        if show: self.progress_frame.pack(fill='x', padx=5, pady=2, before=self.notebook)
        else: self.progress_frame.pack_forget()

    def _on_tz_changed(self):
        self.config['timezone'] = self.timezone_var.get(); save_config(self.config)
        self._add_feed_line(f"Timezone changed to {self.timezone_var.get()}", 'character')
        self._refresh_zones(); self.tz_label.config(text=f"TZ: {self.timezone_var.get()}")

    def _load_file_background(self, log_path: str, monitor_after: bool = False):
        if self.loading_active: messagebox.showwarning("Loading", "Already loading a file."); return
        if not os.path.exists(log_path): messagebox.showerror("Error", f"Log file not found:\n{log_path}"); return
        if self.monitor: self.monitor.stop(); self.monitor = None
        log_date = datetime.fromtimestamp(os.path.getmtime(log_path)).strftime('%Y-%m-%d')
        self.log_date_label.config(text=f"Log Date: {log_date}")
        self.loading_active = True; self._show_progress(True)
        self.monitor_btn.config(state='disabled'); self.status_label.config(text="Status: Loading...", foreground='red')
        self._add_feed_line(f"Loading: {log_path}", 'character')
        def on_progress(p, m): self.root.after(0, lambda: (self.progress_var.set(p), self.progress_label.config(text=m)))
        def on_complete(ok, msg, lines, events, char, zone):
            self.root.after(0, lambda: self._on_load_complete(ok, msg, char, zone, log_path, monitor_after))
        self.loader = BackgroundLoader(self.data_store, on_progress, on_complete)
        self.loader.load_file(log_path, log_date)

    def _on_load_complete(self, success, msg, char, zone, log_path, monitor_after):
        self.loading_active = False; self._show_progress(False); self.monitor_btn.config(state='normal')
        if success:
            self._add_feed_line(msg, 'character')
            stats = self.data_store.get_stats()
            self._add_feed_line(f"Data loaded: {stats['zones']} zones, {stats['events']} events, {stats['players']} players", 'character')
            if char: self.current_character = char; self.char_label.config(text=f"Character: {char}")
            if zone: self.current_zone = zone; self.zone_label.config(text=f"Zone: {zone}")
            if monitor_after and log_path == self.log_path:
                file_size = os.path.getsize(log_path)
                self.parser.reset()
                self.parser.set_log_date(datetime.fromtimestamp(os.path.getmtime(log_path)).strftime('%Y-%m-%d'))
                self.parser.current_character = self.current_character
                self.parser.current_zone = ZoneInfo(zone, datetime.now(), char) if zone and char else None
                if char:
                    zid = self.data_store.get_current_zone_id(char)
                    if zid: self.parser.current_zone_id = self.current_zone_id = zid
                self.monitor = LogMonitor(log_path, self.parser); self.monitor.start(from_position=file_size)
                self.monitoring_active = True
                self.monitor_btn.config(text="⏹ Stop Monitoring"); self.status_label.config(text="Status: Monitoring", foreground='green')
                self._add_feed_line("Now monitoring for new data...", 'character')
            else: self.status_label.config(text="Status: Idle", foreground='red')
            self._refresh_all()
        else: self._add_feed_line(msg, 'error'); self.status_label.config(text="Status: Error", foreground='red')

    def _import_log_file(self):
        fp = filedialog.askopenfilename(title="Import Log File", filetypes=[("Log files","*.log"),("All files","*.*")])
        if fp: self._load_file_background(fp, monitor_after=False)

    def _toggle_monitoring(self):
        if self.monitoring_active: self._stop_monitoring()
        else: self._start_monitoring()

    def _start_monitoring(self):
        if not os.path.exists(self.log_path): messagebox.showerror("Error", f"Log file not found:\n{self.log_path}"); return
        log_date = datetime.fromtimestamp(os.path.getmtime(self.log_path)).strftime('%Y-%m-%d')
        self.log_date_label.config(text=f"Log Date: {log_date}"); self.parser.set_log_date(log_date)
        if self.current_character:
            self.parser.current_character = self.current_character
            zid = self.data_store.get_current_zone_id(self.current_character)
            if zid: self.parser.current_zone_id = self.current_zone_id = zid
            if self.current_zone: self.parser.current_zone = ZoneInfo(self.current_zone, datetime.now(), self.current_character)
        self.monitor = LogMonitor(self.log_path, self.parser)
        self.monitor.start(from_position=os.path.getsize(self.log_path))
        self.monitoring_active = True
        self.monitor_btn.config(text="⏹ Stop Monitoring"); self.status_label.config(text="Status: Monitoring", foreground='green')
        self._add_feed_line(f"Monitoring: {self.log_path}", 'character')

    def _stop_monitoring(self):
        if self.monitor: self.monitor.stop(); self.monitor = None
        self.monitoring_active = False
        self.monitor_btn.config(text="▶ Start Monitoring"); self.status_label.config(text="Status: Idle", foreground='red')
        self._add_feed_line("Monitoring stopped", 'error')

    def _start_event_processor(self):
        def process():
            try:
                while True:
                    evt = self.event_queue.get_nowait()
                    if evt[0] == 'character':
                        self.current_character = evt[1]; self.char_label.config(text=f"Character: {evt[1]}")
                        self._add_feed_line(f"Character: {evt[1]}", 'character')
                    elif evt[0] == 'zone':
                        self.current_zone, ts = evt[1], evt[2]
                        self.current_zone_id = self.parser.current_zone_id
                        self.zone_label.config(text=f"Zone: {evt[1]}")
                        self._add_feed_line(f"[{self._format_time(ts)}] Zone: {evt[1]}", 'zone')
                        self._refresh_zones()
                    elif evt[0] == 'damage':
                        e = evt[1]; total = e.health_dmg + e.armor_dmg
                        self._add_feed_line(f"[{self._format_time(e.timestamp)}] {e.player_name}: {total:,} dmg → {e.npc_name}", 'damage')
                        if self.mini_window: self._update_mini()
                    elif evt[0] == 'error': self._add_feed_line(f"Error: {evt[1]}", 'error')
            except queue.Empty: pass
            self.root.after(100, process)
        self.root.after(100, process)

    def _add_feed_line(self, text: str, tag: str = None):
        self.feed_text.config(state='normal'); self.feed_text.insert('end', text + '\n', tag)
        self.feed_text.see('end'); self.feed_text.config(state='disabled')
        if int(self.feed_text.index('end-1c').split('.')[0]) > 1000:
            self.feed_text.config(state='normal'); self.feed_text.delete('1.0', '500.0'); self.feed_text.config(state='disabled')

    def _start_auto_refresh(self):
        self._refresh_counter = 0
        def refresh():
            if not self.loading_active:
                self._refresh_counter += 1
                if self.monitoring_active and self._refresh_counter >= 5: self._refresh_counter = 0; self._refresh_zones()
                self._refresh_rolling()
                if self.mini_window: self._update_mini()
            self.root.after(10000, refresh)
        self.root.after(10000, refresh)

    def _refresh_all(self):
        self._refresh_rolling(); self._refresh_zones(); self._refresh_monsters(); self._refresh_aliases()
        self.tz_label.config(text=f"TZ: {self.timezone_var.get()}")

    def _populate_damage_tree(self, tree, data, combat_duration, info_label=None, info_text=""):
        for item in tree.get_children(): tree.delete(item)
        if info_label: info_label.config(text=info_text)
        if not data: return
        total_dmg = sum(d['total_dmg'] for d in data)
        for d in data:
            dps = d['total_dmg'] / combat_duration if combat_duration > 0 else 0
            pct = (d['total_dmg'] / total_dmg * 100) if total_dmg > 0 else 0
            tree.insert('', 'end', values=(d['display_name'], f"{d['health_dmg']:,}", f"{d['armor_dmg']:,}",
                f"{d['total_dmg']:,}", f"{d.get('weighted_aggro', 0):,}", f"{dps:.1f}", f"{pct:.1f}%", d['kills']))

    def _refresh_rolling(self):
        latest = self.data_store.get_latest_damage_timestamp()
        if not latest: self._populate_damage_tree(self.rolling_tree, [], 1, self.rolling_info, "No damage data"); return
        try: minutes = float(self.window_var.get())
        except: minutes = 5
        data = group_damage_by_alias(self.data_store.get_damage_in_time_range(latest - timedelta(minutes=minutes), latest))
        if not data: self._populate_damage_tree(self.rolling_tree, [], 1, self.rolling_info, f"No damage in last {minutes:.0f} min"); return
        first_hits = [d['first_hit'] for d in data if d['first_hit']]
        last_hits = [d['last_hit'] for d in data if d['last_hit']]
        if first_hits and last_hits:
            combat = max((max(last_hits) - min(first_hits)).total_seconds(), 1)
            time_range = f"{self._format_time(min(first_hits))} - {self._format_time(max(last_hits))}"
        else: combat = minutes * 60; time_range = "N/A"
        total = sum(d['total_dmg'] for d in data)
        self._populate_damage_tree(self.rolling_tree, data, combat, self.rolling_info, f"Time: {time_range} | Combat: {combat:.0f}s | Total: {total:,}")

    def _refresh_zones(self):
        saved = set(self.zones_tree.selection())
        for item in self.zones_tree.get_children(): self.zones_tree.delete(item)
        try: min_wis = int(self.min_wisdom_var.get() or 0)
        except: min_wis = 0
        for inst in self.data_store.get_all_zone_instances():
            stats = self.data_store.get_zone_stats(inst['zone_id'])
            if min_wis > stats['wisdom']: continue
            left = self._format_time(inst['left_time']) if inst['left_time'] else "(current)"
            self.zones_tree.insert('', 'end', iid=str(inst['zone_id']), values=(inst['name'], inst['character_name'],
                inst['log_date'] or "--", self._format_time(inst['entered_time']), left, f"{stats['wisdom']:,}", stats['kills'], f"{stats['total_dmg']:,}"))
        for iid in saved:
            if self.zones_tree.exists(iid): self.zones_tree.selection_add(iid)

    def _update_session(self):
        sel = self.zones_tree.selection()
        for item in self.session_tree.get_children(): self.session_tree.delete(item)
        if not sel: self.selected_label.config(text="No zones selected"); return
        zone_ids = [int(s) for s in sel]
        data = group_damage_by_alias(self.data_store.get_damage_by_zones(zone_ids))
        if not data: self.selected_label.config(text=f"{len(zone_ids)} zone(s) selected - No damage data"); return
        first, last, kills = self.data_store.get_zones_combat_times(zone_ids)
        combat = max((last - first).total_seconds(), 1) if first and last else 1
        total = sum(d['total_dmg'] for d in data)
        self.selected_label.config(text=f"{len(zone_ids)} zone(s) | Combat: {combat:.0f}s | Kills: {kills} | Total: {total:,}")
        self._populate_damage_tree(self.session_tree, data, combat)

    def _copy_zones(self, compact: bool):
        sel = self.zones_tree.selection()
        if not sel: self._add_feed_line("Nothing selected", 'error'); return
        zone_ids = [int(s) for s in sel]
        data = group_damage_by_alias(self.data_store.get_damage_by_zones(zone_ids))
        total = sum(d['total_dmg'] for d in data)
        first, last, _ = self.data_store.get_zones_combat_times(zone_ids)
        combat = (last - first).total_seconds() if first and last else 1
        if compact:
            lines = [f"{d['display_name'][:8]}: {format_damage_short(d['total_dmg'])} {format_damage_short(int(d['total_dmg']/combat))}/s {d['total_dmg']/total*100 if total else 0:.0f}%" for d in data]
        else:
            lines = ["Player\tHealth\tArmor\tTotal\tAggro Est\tDPS\t%\tKills"]
            for d in data:
                dps = d['total_dmg'] / combat; pct = d['total_dmg'] / total * 100 if total else 0
                lines.append(f"{d['display_name']}\t{d['health_dmg']:,}\t{d['armor_dmg']:,}\t{d['total_dmg']:,}\t{d.get('weighted_aggro', 0):,}\t{dps:.1f}\t{pct:.1f}%\t{d['kills']}")
        self.root.clipboard_clear(); self.root.clipboard_append('\n'.join(lines))

    def _refresh_aliases(self):
        for item in self.alias_tree.get_children(): self.alias_tree.delete(item)
        filt = self.alias_filter_var.get().strip() or None
        for pid, name, alias in self.data_store.get_all_players(filter_text=filt):
            self.alias_tree.insert('', 'end', iid=str(pid), values=(name, alias or "(no alias)"))

    def _refresh_monsters(self):
        """Refresh the monster tab zone list (uses shared min_wisdom_var)."""
        saved = set(self.monster_zones_tree.selection())
        for item in self.monster_zones_tree.get_children(): self.monster_zones_tree.delete(item)
        try: min_wis = int(self.min_wisdom_var.get() or 0)
        except: min_wis = 0
        for inst in self.data_store.get_all_zone_instances():
            stats = self.data_store.get_zone_stats(inst['zone_id'])
            if min_wis > stats['wisdom']: continue
            left = self._format_time(inst['left_time']) if inst['left_time'] else "(current)"
            self.monster_zones_tree.insert('', 'end', iid=str(inst['zone_id']), values=(inst['name'], inst['character_name'],
                inst['log_date'] or "--", self._format_time(inst['entered_time']), left, f"{stats['wisdom']:,}", stats['kills'], f"{stats['total_dmg']:,}"))
        for iid in saved:
            if self.monster_zones_tree.exists(iid): self.monster_zones_tree.selection_add(iid)
        # Refresh monster list
        self._update_monster_list()

    def _refresh_zones_and_monsters(self):
        """Refresh both Zone Runs and Monsters tabs (shared filter)."""
        self._refresh_zones()
        self._refresh_monsters()

    def _update_monster_list(self):
        """Update the monster list based on selected zones and filter."""
        # Clear existing items
        for item in self.monster_tree.get_children(): self.monster_tree.delete(item)
        
        sel = self.monster_zones_tree.selection()
        if not sel:
            self.monster_summary_label.config(text="No zones selected")
            return
        
        zone_ids = [int(s) for s in sel]
        filter_text = self.monster_name_filter_var.get().strip() or None
        
        data = self.data_store.get_monster_summary_by_zones(zone_ids, filter_text)
        if not data:
            self.monster_summary_label.config(text=f"{len(zone_ids)} zone(s) selected - No monster data")
            return
        
        total_dmg = sum(d['total_dmg'] for d in data)
        total_kills = sum(d['kill_count'] for d in data)
        self.monster_summary_label.config(text=f"{len(zone_ids)} zone(s) | {len(data)} monster types | {total_kills} kills | {total_dmg:,} total damage")
        
        # Populate monster tree with new columns
        for d in data:
            iid = self.monster_tree.insert('', 'end', text='', values=(
                d['npc_name'],
                f"{d['health_dmg']:,}",
                f"{d['armor_dmg']:,}",
                f"{d['total_dmg']:,}",
                '--',  # Aggro Est will be shown in expanded player view
                d['kill_count']
            ))
            # Add a dummy child to make it expandable
            self.monster_tree.insert(iid, 'end', text='_placeholder_', values=('','','','','',''))

    def _on_monster_expand(self, event):
        """Load player damage breakdown when a monster item is expanded."""
        item = self.monster_tree.focus()
        if not item:
            return
        
        # Get monster name from the item
        values = self.monster_tree.item(item, 'values')
        if not values:
            return
        npc_name = values[0]
        
        # Check if already loaded (first child text is not placeholder)
        children = self.monster_tree.get_children(item)
        if children:
            first_child_text = self.monster_tree.item(children[0], 'text')
            if first_child_text != '_placeholder_':
                return  # Already loaded
        
        # Remove the dummy child
        for child in children:
            self.monster_tree.delete(child)
        
        # Get zone selection
        sel = self.monster_zones_tree.selection()
        if not sel:
            return
        zone_ids = [int(s) for s in sel]
        
        # Get player damage summary for this monster
        players = self.data_store.get_monster_player_summary(zone_ids, npc_name)
        
        if not players:
            self.monster_tree.insert(item, 'end', text='', values=('No player data','','','','',''))
            return
        
        # Calculate total for percentage
        total_dmg = sum(p['total_dmg'] for p in players)
        
        # Add each player as a child
        for p in players:
            pct = (p['total_dmg'] / total_dmg * 100) if total_dmg > 0 else 0
            self.monster_tree.insert(item, 'end', text='', values=(
                f"  {p['display_name']}",
                f"{p['health_dmg']:,}",
                f"{p['armor_dmg']:,}",
                f"{p['total_dmg']:,} ({pct:.1f}%)",
                f"{p['weighted_aggro']:,}",
                p['kills']
            ))

    def _edit_alias(self, event):
        sel = self.alias_tree.selection()
        if not sel: return
        pid = int(sel[0]); vals = self.alias_tree.item(sel[0], 'values')
        cur = vals[1] if vals[1] != "(no alias)" else ""
        new = simpledialog.askstring("Edit Alias", f"Enter alias for '{vals[0]}':\n(Leave blank to remove)", initialvalue=cur)
        if new is not None:
            self.data_store.update_player_alias(pid, new)
            self._refresh_aliases(); self._refresh_rolling(); self._update_session()

    def _export_csv(self):
        sel = self.zones_tree.selection()
        if sel: zone_ids = [int(s) for s in sel]; default = f"damage_zones_{len(zone_ids)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        else:
            insts = self.data_store.get_all_zone_instances()
            zone_ids = [z['zone_id'] for z in insts]; default = f"damage_all_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        data = group_damage_by_alias(self.data_store.get_damage_by_zones(zone_ids)) if zone_ids else []
        fp = filedialog.asksaveasfilename(title="Export", defaultextension=".csv", filetypes=[("CSV","*.csv")], initialfile=default)
        if not fp: return
        try:
            with open(fp, 'w') as f:
                f.write("Player,Health Damage,Armor Damage,Total Damage,Aggro Est,Kills\n")
                for d in data: f.write(f"{d['display_name']},{d['health_dmg']},{d['armor_dmg']},{d['total_dmg']},{d.get('weighted_aggro', 0)},{d['kills']}\n")
            messagebox.showinfo("Export Complete", f"Data exported to:\n{fp}")
        except Exception as e: messagebox.showerror("Export Error", str(e))

    def _clear_all_data(self):
        if messagebox.askyesno("Clear All Data", "Clear all damage data?\n(Aliases preserved)"):
            self.data_store.clear_all_data(); self._add_feed_line("All data cleared", 'error'); self._refresh_all()

    def _open_mini_window(self):
        if self.mini_window: self.mini_window.lift(); return
        self.mini_window = tk.Toplevel(self.root); self.mini_window.title("AnatomyDPS - Compact")
        self.mini_window.geometry("400x300"); self.mini_window.attributes('-topmost', True)
        self.mini_window.protocol("WM_DELETE_WINDOW", self._close_mini)
        self.mini_view_mode = tk.StringVar(value='zone')
        header = ttk.Frame(self.mini_window); header.pack(fill='x', padx=5, pady=5)
        ttk.Radiobutton(header, text="Current Zone", variable=self.mini_view_mode, value='zone', command=self._update_mini).pack(side='left', padx=2)
        ttk.Radiobutton(header, text="Last 5 Min", variable=self.mini_view_mode, value='5min', command=self._update_mini).pack(side='left', padx=2)
        self.mini_info = ttk.Label(self.mini_window, text="--"); self.mini_info.pack(fill='x', padx=5)
        self.mini_tree = create_treeview(self.mini_window, ('Player','Damage','DPS','%'), [150,80,70,60], height=10)
        scroll = ttk.Scrollbar(self.mini_window, orient='vertical', command=self.mini_tree.yview)
        self.mini_tree.configure(yscrollcommand=scroll.set)
        self.mini_tree.pack(side='left', fill='both', expand=True, padx=(5,0), pady=5); scroll.pack(side='right', fill='y', padx=(0,5), pady=5)
        self._update_mini()

    def _close_mini(self):
        if self.mini_window: self.mini_window.destroy(); self.mini_window = None

    def _update_mini(self):
        if not self.mini_window: return
        for item in self.mini_tree.get_children(): self.mini_tree.delete(item)
        if self.mini_view_mode.get() == 'zone':
            if not self.current_character: self.mini_info.config(text="No character"); return
            zid = self.data_store.get_current_zone_id(self.current_character)
            if not zid: self.mini_info.config(text="No active zone"); return
            data = group_damage_by_alias(self.data_store.get_damage_by_zones([zid]))
            if not data: self.mini_info.config(text=f"Zone: {self.current_zone or '--'} | No damage"); return
            first, last, kills = self.data_store.get_zones_combat_times([zid])
            combat = max((last - first).total_seconds(), 1) if first and last and kills > 0 else 1
            zone_name = (self.current_zone or '--')[:17] + "..." if len(self.current_zone or '--') > 20 else (self.current_zone or '--')
            total = sum(d['total_dmg'] for d in data)
            self.mini_info.config(text=f"{zone_name} | {kills} kills | {total:,} dmg | {combat:.0f}s")
        else:
            latest = self.data_store.get_latest_damage_timestamp()
            if not latest: self.mini_info.config(text="No damage data"); return
            data = group_damage_by_alias(self.data_store.get_damage_in_time_range(latest - timedelta(minutes=5), latest))
            if not data: self.mini_info.config(text="No damage in last 5 min"); return
            first_hits = [d['first_hit'] for d in data if d['first_hit']]
            last_hits = [d['last_hit'] for d in data if d['last_hit']]
            combat = max((max(last_hits) - min(first_hits)).total_seconds(), 1) if first_hits and last_hits else 300
            total = sum(d['total_dmg'] for d in data)
            self.mini_info.config(text=f"Last 5 min | {total:,} dmg | {combat:.0f}s")
        for d in data:
            dps = d['total_dmg'] / combat if combat > 0 else 0
            pct = (d['total_dmg'] / total * 100) if total > 0 else 0
            self.mini_tree.insert('', 'end', values=(d['display_name'], f"{d['total_dmg']:,}", f"{dps:.0f}", f"{pct:.1f}%"))

    def _on_close(self):
        self._stop_monitoring()
        if self.loader: self.loader.cancel()
        self._close_mini(); self.root.destroy()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close); self.root.mainloop()


if __name__ == '__main__':
    import sys
    app = DamageParserGUI(sys.argv[1] if len(sys.argv) > 1 else None)
    app.run()
