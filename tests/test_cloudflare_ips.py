import pytest
from scripts.cloudflare_ips import render

@pytest.mark.parametrize('ipv4,ipv6',[
    (['0.0.0.0/0'],['2606:4700::/32']),
    (['127.0.0.0/8'],['2606:4700::/32']),
    (['173.245.48.0/20'],[]),
    (['2606:4700::/32'],['2606:4700::/32']),
])
def test_refuses_unsafe_or_incomplete_trusted_proxy_list(ipv4,ipv6):
    with pytest.raises(ValueError):
        render({'success':True,'result':{'ipv4_cidrs':ipv4,'ipv6_cidrs':ipv6}})

def test_renders_only_validated_networks():
    output=render({'success':True,'result':{'ipv4_cidrs':['173.245.48.0/20'],'ipv6_cidrs':['2606:4700::/32']}})
    assert output.splitlines()[1:]==['set_real_ip_from 173.245.48.0/20;','set_real_ip_from 2606:4700::/32;']
