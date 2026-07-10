from types import SimpleNamespace

from ui.app import ZhiboApp


def test_tab_activated_updates_current_tag_for_sub_tab():
    app = object.__new__(ZhiboApp)
    app._id_to_tag = {"tab-0": "全部", "tab-1": "ASMR"}
    app._current_tag = None
    refreshed = {"called": False}
    app._refresh_current_table = lambda: refreshed.__setitem__("called", True)

    app.on_tabbed_content_tab_activated(SimpleNamespace(pane=SimpleNamespace(id="tab-1")))

    assert app._current_tag == "ASMR"
    assert refreshed["called"] is True


def test_tab_activated_maps_all_tab_to_none_filter():
    app = object.__new__(ZhiboApp)
    app._id_to_tag = {"tab-0": "全部", "tab-1": "ASMR"}
    app._current_tag = "ASMR"
    app._refresh_current_table = lambda: None

    app.on_tabbed_content_tab_activated(SimpleNamespace(pane=SimpleNamespace(id="tab-0")))

    assert app._current_tag is None
