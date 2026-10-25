function FindProxyForURL(url, host) {
    if (host === "www.costco.com.cn" || host === "ltm.costco.com.cn") {
        return "PROXY 127.0.0.1:18080";
    }
    return "DIRECT";
}
