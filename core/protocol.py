"""
PVTK Network Protocol Constants (Opcodes & Types)
"""

# Auth & Account Opcodes
LC_CHECKEDITION = 0x1388           # 5000: Master gateway version check
LC_PLAYER_LOGIN = 0x138A           # 5002: Master account login (username, password)
CG_LOGIN_ACCOUNT = 0x138A          # 5002: Master login
CG_CHOOSE_SERVER = 0x2714          # 10004: Choose game server

CG_LOGIN_GAMESERVER = 0x0001       # 1: Game server handshake with (ukey, sessionID)
CG_LOGIN = 0x0001
CG_LOGIN_PLAYERCHECK = 0x0001
CG_GET_PLAYER_LIST = 0x2711        # 10001: Request list of characters (up to 3)
CG_LOGIN_ACTORLIST = 0x2711
CG_SELECT_PLAYER = 0x0002          # 2: Select character to enter game
CG_LOGIN_ACTORENTER = 0x0002
CG_LOGOUT = 0x0003                 # 3: Leave character to character select screen
CG_LOGIN_ACTORLEAVE = 0x0003

# Character Creation & Management
CG_LOGIN_CREATEACTOR = 0x2712      # 10002: Create character
CG_LOGIN_CREATEACTOR_AUTO = 0x2715 # 10005: Auto create character

# Scene & Map Opcodes
GC_SCENE_ENTERMAP = 0x2905         # 10501: Scene enter map
CG_SCENE_ENTERMAP = 0x2905
CG_SCENE_LEAVEMAP = 0x2906         # 10502: Leave map
CG_SCENE_PLAYER_MOVING = 0x2908    # 10504: Player moving (X, Y)
CG_SCENE_GO_MAP = 0x290A           # 10506: Teleport / jump map
CG_SCENE_PLAYER_EVENT = 0x290C     # 10508: NPC dialog event choice / menu option
CG_SCENE_GETNPC = 0x2917           # 10519: Talk / interact with NPC
CG_SCENE_WORLDMAP = 0x2919         # 10521: World map open
CG_SCENE_WORLDMAP_DATA = 0x291A    # 10522: World map area sub-list
CG_SCENE_WORLDMAP_ENTER = 0x291B   # 10523: World map teleport / enter
GC_ACTOR_MYACTOR_INFO = 0x2713     # 10003: Actor info received

# Quest / Mission Opcodes
CG_TASK_GETMISSIONDATA = 0x38A5    # 14501: Get quest mission data
CG_TASK_ACCEPT = 0x38A6            # 14502: Accept quest
CG_TASK_DELIVER = 0x38A7           # 14503: Deliver / complete quest (receive EXP/items)
CG_TASK_ABANDON = 0x38A8           # 14504: Abandon quest
CG_TASK_NPC_STATUS = 0x38A9        # 14505: Request NPC quest status markers

# Auto-Sell & Inventory Opcodes
CG_ACTOR_PLAYERBAG = 0x2EE1        # 12001: Open bag / inventory
CG_BAG_CLEAN = 0x2EE3              # 12003: Clean / sort bag
CG_ITEMSHOP_BUY = 0x2EE5           # 12005: Buy item from shop
CG_ITEMSHOP_SELL = 0x2EE6          # 12006: Sell single item
CG_GET_AUTO_SELL_LIST = 0x2F04     # 12036: Get list of eligible trash items for auto sell
CG_AUTO_SELL = 0x2F05              # 12037: Batch auto-sell all trash items (Bán auto)

# Skill & Talent Opcodes
CG_SKILL_SHOP_LIST = 0x36B1        # 14001: List learnable skills at trainer
CG_SKILL_SHOP_LEARN = 0x36B2       # 14002: Learn new skill from trainer
CG_SKILL_AUTO_ACTIVE = 0x36B4      # 14004: Enable auto skill
CG_SKILL_SCROLL_LEARN = 0x36B7     # 14007: Learn skill from book/scroll
CG_SKILL_INITIATIVE_SET = 0x36B9   # 14009: Bind active skill to shortcut slot
CG_SKILL_UPDATE = 0x36BB           # 14011: Upgrade skill level

# Combat & Battle Opcodes
CG_FIGHT_ENTER_LOCALBATTEL = 0x30D5 # 12501: Enter monster / instance battle
CG_FIGHT_RUN_LOCALBATTLE = 0x30D6   # 12502: Run battle round
CG_FIGHT_ENTER_REMOTEBATTLE = 0x30D7 # 12503: Remote team battle
CG_FIGHT_BATTLE_DOPLAN = 0x30D9     # 12505: Execute battle combat plan (skill/attack)
CG_TASK_TEAM_BOSS_FIGHT = 0x38B8    # 14520: Team boss fight
CG_TASK_COUNTRY_BOSS_FIGHT = 0x38CF # 14543: Country boss fight
CG_TASK_ARENA_FIGHT = 0x38BF        # 14527: Arena battle fight
CG_FIGHT_EXIT_BATTLE = 0x38E7       # 14567: Escape / exit battle

# Team & Party Opcodes
CG_TEAM_INVITE = 0x34BD             # 13501: Invite player to team / request join
CG_TEAM_JOIN = 0x34BE               # 13502: Accept and join team
CG_TEAM_LEAVE = 0x34BF              # 13503: Kick or leave team
CG_TEAM_DISBAND = 0x34C1            # 13505: Disband team
CG_TEAM_NEW = 0x34C4                # 13508: Create new team

# Heartbeat & Keep-Alive
CG_SYSTEM_HEART = 0x3E81           # 16001: Keep-alive heartbeat ping (every 20s)

# Chat & Lucky Money (Lì Xì) Opcodes
CG_CHAT_MAP = 0x34C6                    # 13510: Map / Area chat
CG_CHAT_WORLD = 0x34C7                  # 13511: World chat (Thế giới)
CG_CHAT_COUNTRY = 0x34C8                # 13512: Clan / Country chat
CG_CHAT_TEAM = 0x34C9                   # 13513: Team / Party chat
CG_CHAT_PM = 0x34CA                     # 13514: Private message (Mật)
CG_CHAT_BROADCAST = 0x34CB              # 13515: System / Broadcast / Speaker
CG_CHAT_OTHER = 0x34EE                  # 13550: Other channel
CG_CHAT_NOTICE = 0x34F2                 # 13554: Server notice
CG_LUCK_MONEY = 0x34F6                  # 13558: Lucky money (Action 1 = Send, Action 2 = Snatch/Claim)

# Quiz / Question Event Opcodes
CG_VIP_QUESTION_GET = 0x2B30       # 11056: Request question in quiz event
CG_VIP_QUESTION_SUB = 0x2B31       # 11057: Submit answer to quiz event

# Country / Nation Opcodes
CG_COUNTRY_CREATE = 0x3A99             # 15001: Create country
CG_COUNTRY_INFO_BROWSE = 0x3A9A        # 15002: Browse country info
CG_COUNTRY_LIST = 0x3A9C               # 15004: List countries on server
CG_COUNTRY_ENTER = 0x3AA1              # 15009: Enter Country Map (Jump country)
CG_COUNTRY_APPLY = 0x3AA3              # 15011: Apply to join country
CG_COUNTRY_APPLY_LIST = 0x3AA4         # 15012: List applicants for country
CG_COUNTRY_APPLY_DEAL = 0x3AA5         # 15013: Accept/Reject country applicant (-1 for all)
CG_COUNTRY_DEL_MEM = 0x3AA6            # 15014: Kick country member
CG_COUNTRY_LEAVE = 0x3AA7              # 15015: Leave country
CG_COUNTRY_MEMBERS = 0x3AA8            # 15016: List country members
CG_COUNTRY_ADJUST_JOB = 0x3AAA         # 15018: Promote / Adjust member job
CG_COUNTRY_UPGRADE_BUILD = 0x3AAC      # 15020: Upgrade country building
CG_COUNTRY_PEOPLE_DONATE = 0x3AAF      # 15023: Donate to country (Gold, Silver, Resources)
CG_COUNTRY_STORAGE_PUT = 0x3AB0        # 15024: Put item into country storage
CG_COUNTRY_STORAGE_LIST = 0x3AB2       # 15026: List country storage items
CG_COUNTRY_STORAGE_GET = 0x3AB3        # 15027: Get item from country storage
CG_COUNTRY_GET_VALID_MISSION = 0x3AB9  # 15033: Get valid country missions (Hôm nay)
CG_COUNTRY_MAIN_MENU = 0x3ABD          # 15037: Get country main menu / info
CG_COUNTRY_GET_VIP_ITEM = 0x3AE2       # 15074: Claim daily VIP country salary/reward

# Client Build & Handset Constants
CLIENT_BUILD_VERSION = 0x5593C     # 350524
CLIENT_ACTOR_VERSION = 0x51003E    # 5308478
DEFAULT_CLIENT_IP = "123.30.133.164"
DEFAULT_OS = "Android"
DEFAULT_HANDSET = ""

