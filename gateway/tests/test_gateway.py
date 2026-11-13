import json

from bw_gateway import blockpage, extract, netconf

NET = netconf.Net(lan_if="eth0", lan_cidr="192.168.0.0/24", router_ip="192.168.0.1", node_ip="192.168.0.5",
                  vip="192.168.0.2", peers=["192.168.0.6"])


def dev(mac, ip, **kw):
    base = {"mac": mac, "ip": ip, "static": True, "known": True, "tier": "strict", "route": "gateway",
            "inspect": False, "locked": False, "lock_reason": "", "lock_until": 0}
    return {**base, **kw}


STATE = {"quarantine": True, "devices": [
    dev("aa:00:00:00:00:01", "192.168.0.50", inspect=True),
    dev("aa:00:00:00:00:02", "192.168.0.51", locked=True, inspect=True, lock_reason="bedtime"),
    dev("aa:00:00:00:00:03", "192.168.0.60", tier="basic", route="router"),
    dev("aa:00:00:00:00:04", "192.168.0.61", tier="none", route="router", locked=True),
    dev("aa:00:00:00:00:05", "", static=False, known=False),
]}


def test_nft_sets_follow_policy():
    nft = netconf.render_nft(NET, STATE)
    assert "set strict_macs { type ether_addr; elements = { aa:00:00:00:00:01, aa:00:00:00:00:02, aa:00:00:00:00:05 } }" in nft
    assert "set relaxed_macs { type ether_addr; elements = { aa:00:00:00:00:03, aa:00:00:00:00:04 } }" in nft
    # only gateway-routed devices can be cut off at the firewall; the adult's lock is enforced by DNS instead
    assert "set locked_macs { type ether_addr; elements = { aa:00:00:00:00:02 } }" in nft
    assert "set locked_ips { type ipv4_addr; elements = { 192.168.0.51 } }" in nft
    # a locked device is not sent to the proxy
    assert "set inspect_macs { type ether_addr; elements = { aa:00:00:00:00:01 } }" in nft
    assert nft.startswith("table inet breakwater\ndelete table inet breakwater\n")  # atomic replace


def test_nft_unknown_devices_follow_quarantine():
    on = netconf.render_nft(NET, {"quarantine": True, "devices": []})
    off = netconf.render_nft(NET, {"quarantine": False, "devices": []})
    assert "th dport 53 redirect to :5301" in on and "ip saddr 192.168.0.0/24 ip daddr != 192.168.0.0/24 jump no_bypass" in on
    assert "th dport 53 redirect to :5302" in off and "ether saddr @strict_macs ip daddr != 192.168.0.0/24 jump no_bypass" in off
    assert "elements" not in on.split("set doh_v4")[0]  # empty sets render without an elements clause


def test_dhcp_routes_kids_through_breakwater():
    conf = netconf.render_dnsmasq(NET, STATE, "/run/h", "/data/l", "/app/hook")
    assert "port=0" in conf and "dhcp-option=option:dns-server,192.168.0.2" in conf
    assert "dhcp-option=option:router,192.168.0.2" in conf  # quarantine: unknown devices via the virtual IP
    assert "dhcp-option=tag:via_router,option:router,192.168.0.1" in conf
    assert "dhcp-range=192.168.0.100,192.168.0.220,255.255.255.0,2h" in conf
    hosts = netconf.render_dhcp_hosts(STATE).splitlines()
    assert "aa:00:00:00:00:01,set:via_gateway,192.168.0.50" in hosts
    assert "aa:00:00:00:00:03,set:via_router,192.168.0.60" in hosts
    assert not any("00:05" in h for h in hosts)  # unknown devices get the default
    open_conf = netconf.render_dnsmasq(NET, {"quarantine": False}, "/run/h", "/data/l", "/app/hook")
    assert "dhcp-option=option:router,192.168.0.1" in open_conf


def test_keepalived_and_leases():
    conf = netconf.render_keepalived(NET, "/app/health.sh")
    assert "192.168.0.2/24 dev eth0" in conf and "unicast_src_ip 192.168.0.5" in conf and "192.168.0.6" in conf
    leases = netconf.render_leases([{"mac": "aa:00:00:00:00:01", "ip": "192.168.0.50", "hostname": "", "expires": 0}], 1000)
    assert leases == "8200 aa:00:00:00:00:01 192.168.0.50 * *\n"
    single = netconf.Net(lan_if="eth0", lan_cidr="10.0.0.0/24", router_ip="10.0.0.1", node_ip="10.0.0.2")
    assert single.vip == "10.0.0.2" and not single.clustered


def test_search_extraction():
    assert extract.search_query("www.google.ca", "/search", "q=how+do+volcanoes+work&hl=en") == ("Google", "how do volcanoes work")
    assert extract.search_query("www.bing.com", "/search", "q=lego") == ("Bing", "lego")
    assert extract.search_query("duckduckgo.com", "/", "q=cats&ia=web") == ("DuckDuckGo", "cats")
    assert extract.search_query("www.youtube.com", "/results", "search_query=minecraft") == ("YouTube", "minecraft")
    assert extract.search_query("www.google.com", "/maps", "q=victoria") is None
    assert extract.search_query("notgoogle.example.com", "/search", "q=x") is None
    assert extract.search_query("www.google.com", "/search", "q=+") is None
    assert extract.youtube_search_body("/youtubei/v1/search", json.dumps({"query": "hockey  goals"}).encode()) == "hockey goals"


def test_youtube_extraction():
    assert extract.youtube_video_id("www.youtube.com", "/watch", "v=dQw4w9WgXcQ&t=3") == "dQw4w9WgXcQ"
    assert extract.youtube_video_id("www.youtube.com", "/shorts/abc123XYZ_-", "") == "abc123XYZ_-"
    assert extract.youtube_video_id("youtu.be", "/dQw4w9WgXcQ", "") == "dQw4w9WgXcQ"
    assert extract.youtube_video_id("www.youtube.com", "/feed/subscriptions", "") is None
    body = json.dumps({"videoDetails": {"videoId": "abc123def45", "title": "T", "author": "Chan", "lengthSeconds": "61"}})
    assert extract.youtube_player_details(body.encode()) == {"vid": "abc123def45", "title": "T", "channel": "Chan", "dur": 61}
    assert extract.youtube_player_details(b"not json") is None
    html = '<script>var x = {"videoDetails":{"videoId":"abc","title":"T","lengthSeconds":"125","author":"Caf\\u00e9 TV"}}</script>'
    assert extract.youtube_page_details(html) == ("Café TV", 125)
    assert extract.page_title("<html><head><title>\n Fish &amp; Chips </title>") == "Fish & Chips"


def test_keywords_and_safesearch():
    assert extract.keyword_hit(["fortnite"], "www.google.com", "/search", "q=Fortnite+skins") == "fortnite"
    assert extract.keyword_hit(["skin"], "www.google.com", "/search", "q=fortnite+skins") is None  # whole words only
    assert extract.keyword_hit([], "x.com", "/", "") is None
    assert extract.enforce_safesearch("www.google.com", "https://www.google.com/search?q=cats") == \
        "https://www.google.com/search?q=cats&safe=active"
    assert extract.enforce_safesearch("www.google.com", "https://www.google.com/search?q=cats&safe=off") == \
        "https://www.google.com/search?q=cats&safe=active"
    assert extract.enforce_safesearch("www.google.com", "https://www.google.com/search?q=cats&safe=active") is None
    assert extract.enforce_safesearch("www.bing.com", "https://www.bing.com/images/search?q=a") == \
        "https://www.bing.com/images/search?q=a&adlt=strict"
    assert extract.enforce_safesearch("example.com", "https://example.com/search?q=a") is None


def test_passthrough_matches_suffixes():
    hosts = frozenset({"apple.com", "googlevideo.com"})
    assert extract.is_passthrough("gs.apple.com", hosts) and extract.is_passthrough("apple.com", hosts)
    assert not extract.is_passthrough("pineapple.com", hosts)


def test_paused_page_explains_why():
    html = blockpage.render({"lock_reason": "bedtime", "lock_until": 1_800_000_000}, "America/Vancouver")
    assert "bedtime" in html.lower() and "Back at" in html
    assert "Internet is paused" in blockpage.render(None, "UTC")
