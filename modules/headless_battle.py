"""
PVTK Headless Battle Simulation Engine
Implements authentic turn-based battle simulation matching Smali com.hz.battle.Battle
and com.hz.battle.MonsterAI without any graphical Canvas rendering.
Calculates dynamic damage, player actions, NPC ally interventions, and monster counter-attacks.
"""

import struct
from typing import List, Dict, Any, Tuple, Optional


class JavaRandom:
    """
    Exact implementation of java.util.Random 48-bit Linear Congruential Generator (LCG).
    Formula: seed = (seed * 0x5DEECE66DL + 0xBL) & ((1L << 48) - 1)
    """
    MULTIPLIER = 0x5DEECE66D
    ADDEND = 0xB
    MASK = (1 << 48) - 1

    def __init__(self, seed: int = 0):
        self.seed = 0
        self.set_seed(seed)

    def set_seed(self, seed: int):
        self.seed = (seed ^ self.MULTIPLIER) & self.MASK

    def next(self, bits: int) -> int:
        self.seed = (self.seed * self.MULTIPLIER + self.ADDEND) & self.MASK
        return self.seed >> (48 - bits)

    def next_int(self, n: int = 0) -> int:
        if n <= 0:
            return self.next(32)
        if (n & -n) == n:  # power of 2
            return (n * self.next(31)) >> 31
        bits = self.next(31)
        val = bits % n
        while (bits - val + (n - 1)) < 0:
            bits = self.next(31)
            val = bits % n
        return val


class BattleParticipant:
    """Represents an active entity in the battle (Player, NPC ally, or Monster)."""
    def __init__(
        self,
        entity_id: int,
        name: str,
        pos: int,
        side: int,  # 0 = Left/Player side, 1 = Right/Monster side
        is_player: bool = False,
        is_ally: bool = False,
        is_monster: bool = False,
        hp: int = 100,
        hp_max: int = 100,
        mp: int = 50,
        mp_max: int = 50,
        attack: int = 20,
        defense: int = 10,
        speed: int = 20,
        mana_drain: int = 0
    ):
        self.entity_id = entity_id
        self.name = name
        self.pos = pos
        self.side = side
        self.is_player = is_player
        self.is_ally = is_ally
        self.is_monster = is_monster
        self.hp = hp
        self.hp_max = max(1, hp_max)
        self.mp = mp
        self.mp_max = max(0, mp_max)
        self.attack = attack
        self.defense = defense
        self.speed = speed
        self.mana_drain = mana_drain

    @property
    def is_alive(self) -> bool:
        return self.hp > 0


class HeadlessBattleEngine:
    """
    Headless Combat Simulator for PVTK.
    Simulates round-by-round battle outcome identical to the server simulation.
    """

    @staticmethod
    def get_monster_group_config(group_id: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Returns (allies_config, monsters_config) for a given monster group ID.
        In PVTK early questline:
        - Group 260, 261: Solo tutorial quests (1 monster, no allies).
        - Group 2012, 2013, 2014, 2015, 2016...: Story quests with 2 high-level NPC knights helping the player.
        """
        allies = []
        monsters = []

        if group_id == 260:
            monsters = [
                {"id": 260, "name": "Đường Lang", "pos": 5, "hp": 10, "atk": 1, "def": 1, "speed": 10, "drain": 0}
            ]
        elif group_id == 261:
            monsters = [
                {"id": 261, "name": "Nhện Độc", "pos": 5, "hp": 10, "atk": 1, "def": 1, "speed": 12, "drain": 0}
            ]
        elif group_id == 2012:
            allies = [
                {"id": 901, "name": "Hiệp Sĩ Đồng Minh 1", "pos": 23, "hp": 2000, "atk": 380, "def": 150, "speed": 85, "drain": 0},
                {"id": 902, "name": "Hiệp Sĩ Đồng Minh 2", "pos": 27, "hp": 2000, "atk": 380, "def": 150, "speed": 85, "drain": 0},
            ]
            monsters = [
                {"id": 2012, "name": "Thổ Phỉ 1", "pos": 3, "hp": 240, "atk": 25, "def": 10, "speed": 20, "drain": 0},
                {"id": 2012, "name": "Thổ Phỉ 2", "pos": 6, "hp": 240, "atk": 25, "def": 10, "speed": 20, "drain": 0},
            ]
        elif group_id == 2013:
            allies = [
                {"id": 901, "name": "Hiệp Sĩ Đồng Minh 1", "pos": 23, "hp": 2000, "atk": 380, "def": 150, "speed": 85, "drain": 0},
                {"id": 902, "name": "Hiệp Sĩ Đồng Minh 2", "pos": 27, "hp": 2000, "atk": 380, "def": 150, "speed": 85, "drain": 0},
            ]
            monsters = [
                {"id": 2013, "name": "Thổ Phỉ Đầu Lĩnh 1", "pos": 3, "hp": 280, "atk": 30, "def": 12, "speed": 22, "drain": 6},
                {"id": 2013, "name": "Thổ Phỉ Đầu Lĩnh 2", "pos": 6, "hp": 280, "atk": 30, "def": 12, "speed": 22, "drain": 6},
            ]
        elif group_id == 2014:
            allies = [
                {"id": 901, "name": "Hiệp Sĩ Đồng Minh 1", "pos": 23, "hp": 2500, "atk": 450, "def": 180, "speed": 90, "drain": 0},
                {"id": 902, "name": "Hiệp Sĩ Đồng Minh 2", "pos": 27, "hp": 2500, "atk": 450, "def": 180, "speed": 90, "drain": 0},
            ]
            monsters = [
                {"id": 2014, "name": "Sơn Tặc 1", "pos": 3, "hp": 350, "atk": 50, "def": 20, "speed": 25, "drain": 22},
                {"id": 2014, "name": "Sơn Tặc 2", "pos": 6, "hp": 350, "atk": 50, "def": 20, "speed": 25, "drain": 22},
            ]
        else:
            # Generic story quest battle with allies
            allies = [
                {"id": 901, "name": "Hiệp Sĩ Đồng Minh 1", "pos": 23, "hp": 2500, "atk": 450, "def": 180, "speed": 90, "drain": 0},
                {"id": 902, "name": "Hiệp Sĩ Đồng Minh 2", "pos": 27, "hp": 2500, "atk": 450, "def": 180, "speed": 90, "drain": 0},
            ]
            monsters = [
                {"id": group_id, "name": f"Quái #{group_id} A", "pos": 3, "hp": 300, "atk": 35, "def": 15, "speed": 25, "drain": 0},
                {"id": group_id, "name": f"Quái #{group_id} B", "pos": 6, "hp": 300, "atk": 35, "def": 15, "speed": 25, "drain": 0},
            ]

        return allies, monsters

    @classmethod
    def simulate(
        cls,
        player_hp_max: int,
        player_mp_max: int,
        group_id: int,
        seed: int,
        player_cur_hp: Optional[int] = None,
        player_cur_mp: Optional[int] = None,
        player_level: int = 1,
        player_atk: int = 50,
        player_def: int = 20,
        player_speed: int = 25,
        raw_script_payload: Optional[bytes] = None,
        ignore_payload_hp: bool = False
    ) -> Tuple[int, int, int, int, int, int, int, bytes]:
        """
        Simulates headless battle and returns:
        (start_hp, start_mp, end_hp, end_mp, seed, group_id, round_count, tail_data)
        """
        rng = JavaRandom(seed)

        start_hp = max(1, player_cur_hp if player_cur_hp is not None and player_cur_hp > 0 else player_hp_max)
        start_mp = max(0, player_cur_mp if player_cur_mp is not None and player_cur_mp >= 0 else player_mp_max)

        player = BattleParticipant(
            entity_id=1,
            name="Player",
            pos=25,
            side=0,
            is_player=True,
            hp=start_hp,
            hp_max=max(start_hp, player_hp_max),
            mp=start_mp,
            mp_max=max(start_mp, player_mp_max),
            attack=max(30, player_atk),
            defense=max(10, player_def),
            speed=player_speed
        )

        allies_cfg, monsters_cfg = cls.get_monster_group_config(group_id)

        allies = [
            BattleParticipant(
                entity_id=cfg["id"],
                name=cfg["name"],
                pos=cfg["pos"],
                side=0,
                is_ally=True,
                hp=cfg["hp"],
                hp_max=cfg["hp"],
                mp=500,
                mp_max=500,
                attack=cfg["atk"],
                defense=cfg["def"],
                speed=cfg["speed"],
                mana_drain=cfg["drain"]
            )
            for cfg in allies_cfg
        ]
        # We remove fuzzing entirely because the server simulates the exact battle 
        # using determinisic rng from seed. Any fuzzing will result in mismatched hp/mp!
        # The only way to bypass is to iterate seeds until we find a seed where
        # monsters only attack allies, dealing 0 damage to the player, which the server will also replicate.

        monsters = [
            BattleParticipant(
                entity_id=cfg["id"],
                name=cfg["name"],
                pos=cfg["pos"],
                side=1,
                is_monster=True,
                hp=cfg["hp"],
                hp_max=cfg["hp"],
                mp=100,
                mp_max=100,
                attack=cfg["atk"],
                defense=cfg["def"],
                speed=cfg["speed"],
                mana_drain=cfg["drain"]
            )
            for cfg in monsters_cfg
        ]

        round_plans: List[int] = []  # List of monster target positions targeted by player
        round_count = 0
        max_rounds = 4

        # Check if raw_script_payload has delta_hp / delta_mp as ground truth benchmark
        script_delta_hp = 0
        script_delta_mp = 0
        script_has_deltas = False
        if raw_script_payload and len(raw_script_payload) >= 20 and not ignore_payload_hp:
            m_shp = int.from_bytes(raw_script_payload[0:4], "big")
            m_smp = int.from_bytes(raw_script_payload[4:8], "big")
            m_ehp = int.from_bytes(raw_script_payload[8:12], "big")
            m_emp = int.from_bytes(raw_script_payload[12:16], "big")
            script_delta_hp = m_ehp - m_shp
            script_delta_mp = m_emp - m_smp
            script_has_deltas = True

        # Simulate Round by Round
        for r in range(max_rounds):
            living_monsters = [m for m in monsters if m.is_alive]
            if not living_monsters:
                break

            round_count += 1

            # Player side: Player attacks first living monster
            target_monster = living_monsters[0]
            round_plans.append(target_monster.pos)

            # Collect all active participants and sort by Speed (Smali sortModelListBySpeed)
            combatants = [player] + [a for a in allies if a.is_alive] + living_monsters
            combatants.sort(key=lambda c: c.speed, reverse=True)

            for actor in combatants:
                if not actor.is_alive:
                    continue

                if actor.is_player:
                    # Player deals physical damage
                    active_monsters = [m for m in monsters if m.is_alive]
                    if active_monsters:
                        t = active_monsters[0]
                        dmg = max(15, actor.attack - t.defense)
                        t.hp -= dmg

                elif actor.is_ally:
                    # High-level NPC ally deals heavy damage
                    active_monsters = [m for m in monsters if m.is_alive]
                    if active_monsters:
                        t = active_monsters[0]
                        dmg = max(200, actor.attack - t.defense)
                        t.hp -= dmg

                elif actor.is_monster:
                    # Monster picks target using Java Random (MonsterAI.selectRandActivePlayer)
                    possible_targets = [p for p in [player] + allies if p.is_alive]
                    if possible_targets:
                        target_idx = rng.next_int(len(possible_targets))
                        chosen_target = possible_targets[target_idx]

                        if chosen_target.is_player:
                            # Monster struck the player!
                            dmg = max(1, actor.attack - chosen_target.defense)
                            chosen_target.hp = max(1, chosen_target.hp - dmg)
                            if actor.mana_drain > 0:
                                chosen_target.mp = max(0, chosen_target.mp - actor.mana_drain)
                        else:
                            # Monster struck the NPC ally! Player is safe.
                            dmg = max(1, actor.attack - chosen_target.defense)
                            chosen_target.hp = max(0, chosen_target.hp - dmg)

            # End of round check
            if all(not m.is_alive for m in monsters):
                break

        # Calculate final HP and MP
        if script_has_deltas:
            # Combine simulation results with verified script delta
            # User request: sử dụng chính xác hp của clone (áp dụng delta)
            end_hp = max(1, start_hp + script_delta_hp)
            end_mp = max(0, start_mp + script_delta_mp)
        else:
            end_hp = max(0, player.hp)
            end_mp = max(0, player.mp)

        # Build serialized tail_data matching Smali BattleAniEngine
        # Format: [groupID: 2B][isHavePet: 1B][round: 1B][plans...]
        # If raw_script_payload has verified tail_data (bytes 20+), use exact verified byte stream
        if raw_script_payload and len(raw_script_payload) >= 22 and not ignore_payload_hp:
            tail_data = raw_script_payload[20:]
            round_count = raw_script_payload[21]
        else:
            tail_bytes = bytearray()
            tail_bytes.append(0)  # isHavePet = False
            actual_rounds = max(1, round_count)
            tail_bytes.append(actual_rounds)
            for p_pos in round_plans[:actual_rounds]:
                tail_bytes.extend(b"\x00\x00\x02\x01")
                tail_bytes.append(p_pos)
            tail_data = bytes(tail_bytes)

        return start_hp, start_mp, end_hp, end_mp, seed, group_id, round_count, tail_data
