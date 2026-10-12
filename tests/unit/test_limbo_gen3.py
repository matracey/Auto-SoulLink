from tests.unit.gen3_world import World, mon_record

A, B, OT = 0x11111111, 0x22222222, 0xABCD
KEY = "11111111:0000ABCD"


def world():
    w = World("gen3_rr", "radical_red")
    w.set_party([mon_record(A, OT), mon_record(B, OT)])
    return w


def test_qualified_inventories_refresh_periodically_not_every_tick():
    w = world()
    w.step_to(60)
    hello = w.events("hello")[0]
    assert hello["inventory_protocol"] == 1 and hello["inventory_complete"]
    first = hello["inventory_generation"]
    w.step(120)
    assert not any(row.get("inventory_complete") for row in w.events("tick"))
    w.step(180)
    fresh = [row for row in w.events("tick") if row.get("inventory_complete")]
    assert len(fresh) == 1 and fresh[0]["inventory_generation"] > first
    assert len(fresh[0]["party"]) == 2 and "pc_boxes" in fresh[0]


def test_screen_transition_never_qualifies_inventory():
    w = world()
    w.step_to(60)
    w.break_checkpoint()
    w.step(360)
    assert all(row.get("inventory_complete") is False for row in w.events("tick")[-12:])


def test_reconnect_waits_for_control_before_executing_delivered_command():
    w = world()
    w.auto_recovery_control = False
    w.step_to(60)
    w.command(cmd="force_faint", key=KEY)
    w.step(2)
    assert w.party_hp(0) == 20
    w.command(cmd="recovery_control", paused=[], cancelled={})
    w.step()
    assert w.party_hp(0) == 0


def test_partial_box_scan_cannot_establish_absence():
    w = world()
    w.step_to(60)
    w.parts.reads.read_box = lambda *_: None
    w.step(360)
    assert all(not row.get("inventory_complete") for row in w.events("tick"))
