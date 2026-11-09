import Cocoa
import WebKit

// Private stdin/stdout protocol owned by the local CLI. No credentials are logged.
let bridgeVersion = "0.3.0"
if CommandLine.arguments.contains("--version") {
    print("fab-library-macbridge \(bridgeVersion)")
    exit(0)
}

func permitted(_ method: String, _ path: String, _ body: Any?) -> Bool {
    guard path.hasPrefix("/i/"), !path.contains("\\"),
          let url = URL(string: "https://www.fab.com" + path), url.host == "www.fab.com" else { return false }
    let uuid = "[0-9a-fA-F-]{36}"
    func matches(_ pattern: String) -> Bool { url.path.range(of: pattern, options: .regularExpression) != nil }
    if method == "GET" {
        return url.path == "/i/listings/search"
            || matches("^/i/listings/\(uuid)(/prices-infos)?$")
            || matches("^/i/users/me/listings-states/\(uuid)$")
    }
    if method == "POST", matches("^/i/listings/\(uuid)/add-to-library$"),
       let object = body as? [String: Any], object.count == 1,
       let offer = object["offerId"] as? String, !offer.isEmpty, offer.count < 512 { return true }
    return false
}

if CommandLine.arguments.contains("--self-test") {
    let id = "10000000-0000-4000-8000-000000000001"
    precondition(permitted("GET", "/i/listings/\(id)/prices-infos", nil))
    precondition(permitted("POST", "/i/listings/\(id)/add-to-library", ["offerId":"zero"]))
    precondition(!permitted("POST", "/i/checkout", [:]))
    precondition(!permitted("GET", "//evil.invalid/i/listings/search", nil))
    precondition(!permitted("POST", "/i/listings/\(id)/add-to-library", ["offerId":"x", "payment":"x"]))
    print("request allowlist passed")
    exit(0)
}

guard let index = CommandLine.arguments.firstIndex(of: "--profile"),
      CommandLine.arguments.count > index + 1,
      let profile = UUID(uuidString: CommandLine.arguments[index + 1]) else {
    print("{\"ok\":false,\"error\":\"profile_required\"}")
    exit(2)
}

@MainActor final class Bridge: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, NSWindowDelegate {
    let profile: UUID
    var web: WKWebView!
    var window: NSWindow!
    var address: NSTextField!
    var pending: [String: Any]?
    var phase = "idle"
    var authorizationCode: String?
    var timer: Timer?
    var probing = false
    var requestID = 0

    init(profile: UUID) { self.profile = profile }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = WKWebsiteDataStore(forIdentifier: profile)
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 960, height: 700), configuration: configuration)
        web.navigationDelegate = self
        web.uiDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 960, height: 740), styleMask: [.titled, .closable, .resizable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Fab Library — Epic / Fab 登录"
        window.isReleasedWhenClosed = false
        window.delegate = self
        let content = NSView(frame: NSRect(x:0,y:0,width:960,height:740))
        web.autoresizingMask = [.width, .height]
        content.addSubview(web)
        address = NSTextField(labelWithString: "请在官方页面完成登录；密码不会传给 AI。")
        address.frame = NSRect(x:12,y:710,width:770,height:22)
        address.autoresizingMask = [.width, .minYMargin]
        content.addSubview(address)
        let check = NSButton(title:"检查登录", target:self, action:#selector(checkLogin))
        check.frame = NSRect(x:815,y:705,width:130,height:30)
        check.autoresizingMask = [.minXMargin,.minYMargin]
        content.addSubview(check)
        window.contentView = content
        window.center()
        emit(["ready":true,"version":bridgeVersion])
        DispatchQueue.global(qos:.userInitiated).async {
            while let line = readLine() {
                guard let data = line.data(using:.utf8), let object = try? JSONSerialization.jsonObject(with:data) as? [String:Any] else {
                    DispatchQueue.main.async { self.emit(["ok":false,"error":"invalid_request"]) }
                    continue
                }
                DispatchQueue.main.async { self.handle(object) }
            }
            DispatchQueue.main.async { NSApp.terminate(nil) }
        }
    }

    func emit(_ object: [String:Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject:object, options:[.sortedKeys]),
              let text = String(data:data,encoding:.utf8) else { return }
        print(text)
        fflush(stdout)
    }

    func finish(_ object: [String:Any]) {
        timer?.invalidate()
        timer = nil
        pending = nil
        phase = "idle"
        probing = false
        authorizationCode = nil
        window.orderOut(nil)
        emit(object)
    }

    func handle(_ request: [String:Any]) {
        if request["command"] as? String == "continue_login", phase == "awaitingExchange", pending != nil {
            phase = "loginFab"
            authorizationCode = nil
            web.load(URLRequest(url:URL(string:"https://www.fab.com/social/login/epic/")!))
            return
        }
        guard pending == nil else { emit(["ok":false,"error":"busy"]); return }
        pending = request
        requestID += 1
        let generation = requestID
        let login = request["command"] as? String == "login"
        timer = Timer.scheduledTimer(withTimeInterval:login ? 600 : 80, repeats:false) { _ in
            DispatchQueue.main.async { if self.requestID == generation { self.finish(["ok":false,"error":"timeout"]) } }
        }
        if login {
            guard let text = request["login_url"] as? String, let url = URL(string:text),
                  url.scheme == "https", url.host == "www.epicgames.com", url.path == "/id/login" else {
                finish(["ok":false,"error":"invalid_login_url"]); return
            }
            phase = "loginCode"
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps:true)
            web.load(URLRequest(url:url))
            return
        }
        let method = request["method"] as? String ?? "GET"
        let path = request["path"] as? String ?? ""
        guard request["command"] as? String == "fetch", permitted(method,path,request["body"]) else {
            finish(["ok":false,"error":"request_not_allowed"]); return
        }
        if web.url?.host == "www.fab.com", !web.isLoading {
            phase = "fetch"
            performFetch()
        } else {
            phase = "bootstrap"
            web.load(URLRequest(url:URL(string:"https://www.fab.com/robots.txt")!))
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if let url = web.url {
            // Never display query strings: OAuth redirects may include authorization material.
            address.stringValue = "\(url.host ?? "")\(url.path)"
        }
        if phase == "loginCode" { captureCode() }
        else if phase == "loginFab", let url = web.url, url.host == "www.fab.com",
                !url.path.hasPrefix("/social/login/"), !url.path.hasPrefix("/social/complete/"), !url.path.hasPrefix("/login") { probeLogin() }
        else if phase == "bootstrap", web.url?.host == "www.fab.com" { phase = "fetch"; performFetch() }
    }

    @objc func checkLogin() {
        if phase == "loginCode" { captureCode() }
        if phase == "loginFab" { probeLogin() }
    }

    func captureCode() {
        guard let url = web.url, url.host == "www.epicgames.com", url.path == "/id/api/redirect" else { return }
        let generation = requestID
        web.evaluateJavaScript("document.body.innerText", in:nil, in:.defaultClient) { result in
            guard self.requestID == generation, self.phase == "loginCode", case .success(let value) = result,
                  let text = value as? String, let data = text.data(using:.utf8),
                  let object = try? JSONSerialization.jsonObject(with:data) as? [String:Any],
                  let code = object["authorizationCode"] as? String, !code.isEmpty else { return }
            self.phase = "awaitingExchange"
            self.emit(["event":"oauth_code","authorization_code":code])
        }
    }

    func probeLogin() {
        guard !probing, phase == "loginFab", web.url?.host == "www.fab.com" else { return }
        probing = true
        let generation = requestID
        fetch(method:"GET", path:"/i/users/me/listings-states/9e93d929-36e8-45f9-b925-325db1b9385d", body:nil) { response in
            guard self.requestID == generation else { return }
            self.probing = false
            guard self.phase == "loginFab" else { return }
            if response["status"] as? Int == 200,
               let body = response["body"] as? String, let data = body.data(using:.utf8),
               let value = try? JSONSerialization.jsonObject(with:data) as? [String:Any], value["acquired"] is Bool {
                // Delivered only to the parent CLI through its private pipe, never to terminal output.
                self.finish(["ok":true,"fab_session_verified":true])
            } else {
                self.address.stringValue = "尚未确认 Fab 登录。请完成页面验证，然后点击“检查登录”。"
            }
        }
    }

    func performFetch() {
        guard let request = pending else { return }
        let generation = requestID
        fetch(method:request["method"] as? String ?? "GET", path:request["path"] as? String ?? "", body:request["body"]) { response in
            guard self.requestID == generation, self.phase == "fetch" else { return }
            self.finish(["ok":true,"response":response])
        }
    }

    func fetch(method:String, path:String, body:Any?, completion:@escaping ([String:Any])->Void) {
        let script = """
        const init = {method, credentials:'include', headers:{'Accept':'application/json'}, signal:AbortSignal.timeout(40000)};
        if (body !== null) { init.body = JSON.stringify(body); init.headers['Content-Type']='application/json'; }
        if (method === 'POST') {
            const m = document.cookie.match(/(?:^|;\\s*)fab_csrftoken=([^;]+)/);
            if (m) init.headers['X-CSRFToken']=m[1];
        }
        const response = await fetch(path,init);
        const text = await response.text();
        if (text.length > 10000000) throw new Error('response_too_large');
        return {status:response.status,body:text};
        """
        web.callAsyncJavaScript(script, arguments:["method":method,"path":path,"body":body ?? NSNull()], in:nil, in:.defaultClient) { result in
            if case .success(let value) = result, let object = value as? [String:Any] { completion(object) }
            else { completion(["status":0,"body":"request_failed"]) }
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled && pending != nil { finish(["ok":false,"error":"navigation_failed"]) }
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled && pending != nil { finish(["ok":false,"error":"navigation_failed"]) }
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if navigationAction.targetFrame == nil { web.load(navigationAction.request) }
        return nil
    }
    func windowWillClose(_ notification: Notification) {
        if pending != nil { finish(["ok":false,"error":"cancelled"]) }
    }
}

MainActor.assumeIsolated {
    let application = NSApplication.shared
    application.setActivationPolicy(.accessory)
    let delegate = Bridge(profile:profile)
    application.delegate = delegate
    application.run()
}
