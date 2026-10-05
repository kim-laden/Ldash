import QuickLook
import UIKit
import WebKit

/// Full-screen system WebKit view. The page is the shared Laden Ops UI.
final class ShellViewController: UIViewController, WKNavigationDelegate, WKScriptMessageHandler, QLPreviewControllerDataSource, PhoneChrome {
    private let host = PhoneHost()
    private var webView: WKWebView!
    private var bottomPin: NSLayoutConstraint!
    private var previewURL: URL?

    override var preferredStatusBarStyle: UIStatusBarStyle { .lightContent }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = Self.void
        host.chrome = self

        let content = WKUserContentController()
        content.addUserScript(WKUserScript(source: Self.bridgeSource, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        content.add(self, name: "host")
        let config = WKWebViewConfiguration()
        config.userContentController = content
        config.defaultWebpagePreferences.allowsContentJavaScript = true

        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.isOpaque = false
        web.backgroundColor = Self.void
        web.scrollView.backgroundColor = Self.void
        web.scrollView.contentInsetAdjustmentBehavior = .never
        web.allowsLinkPreview = false
        web.allowsBackForwardNavigationGestures = false
        #if DEBUG
        if #available(iOS 16.4, *) {
            web.isInspectable = true
        }
        #endif
        web.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(web)
        let bottom = web.bottomAnchor.constraint(equalTo: view.bottomAnchor)
        NSLayoutConstraint.activate([
            web.topAnchor.constraint(equalTo: view.topAnchor),
            web.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            bottom,
        ])
        webView = web
        bottomPin = bottom
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(keyboard(_:)),
            name: UIResponder.keyboardWillChangeFrameNotification,
            object: nil
        )
        loadPage()
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "host" else { return }
        let raw: String
        if let text = message.body as? String {
            raw = text
        } else {
            return
        }
        guard let data = raw.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        let id = (object["id"] as? NSNumber)?.intValue ?? 0
        let method = object["method"] as? String ?? ""
        let params = object["params"] as? [String: Any] ?? [:]
        host.dispatch(method: method, params: params) { [weak self] result in
            self?.reply(id: id, result: result)
        }
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        if navigationAction.navigationType == .other || navigationAction.navigationType == .reload {
            decisionHandler(.allow)
            return
        }
        let scheme = url.scheme?.lowercased() ?? ""
        if scheme == "http" || scheme == "https" {
            UIApplication.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        if scheme == "file" || scheme == "about" {
            decisionHandler(.allow)
            return
        }
        decisionHandler(.cancel)
    }

    func presentFile(_ url: URL) {
        let show = { [weak self] in
            guard let self else { return }
            self.previewURL = url
            let preview = QLPreviewController()
            preview.dataSource = self
            if self.presentedViewController != nil {
                self.dismiss(animated: false) { self.present(preview, animated: true) }
            } else {
                self.present(preview, animated: true)
            }
        }
        if Thread.isMainThread {
            show()
        } else {
            DispatchQueue.main.async(execute: show)
        }
    }

    func shareFile(_ url: URL) {
        let show = { [weak self] in
            guard let self else { return }
            let activity = UIActivityViewController(activityItems: [url], applicationActivities: nil)
            if let pop = activity.popoverPresentationController {
                pop.sourceView = self.view
                pop.sourceRect = CGRect(x: self.view.bounds.midX, y: self.view.bounds.midY, width: 1, height: 1)
            }
            if self.presentedViewController != nil {
                self.dismiss(animated: false) { self.present(activity, animated: true) }
            } else {
                self.present(activity, animated: true)
            }
        }
        if Thread.isMainThread {
            show()
        } else {
            DispatchQueue.main.async(execute: show)
        }
    }

    func openWeb(_ url: URL) {
        let open = { UIApplication.shared.open(url) }
        if Thread.isMainThread {
            open()
        } else {
            DispatchQueue.main.async(execute: open)
        }
    }

    func numberOfPreviewItems(in controller: QLPreviewController) -> Int {
        previewURL == nil ? 0 : 1
    }

    func previewController(_ controller: QLPreviewController, previewItemAt index: Int) -> QLPreviewItem {
        (previewURL ?? URL(fileURLWithPath: "/")) as NSURL
    }

    private func loadPage() {
        guard let root = Bundle.main.resourceURL?.appendingPathComponent("web", isDirectory: true) else {
            showMissing("The web UI was not copied into the app.")
            return
        }
        let index = root.appendingPathComponent("index.html")
        guard FileManager.default.fileExists(atPath: index.path) else {
            showMissing("Build Laden Ops from the laden-dash folder so web/ is next to iphone/.")
            return
        }
        webView.loadFileURL(index, allowingReadAccessTo: root)
    }

    private func showMissing(_ text: String) {
        let label = UILabel()
        label.text = text
        label.textColor = UIColor(red: 0.90, green: 0.95, blue: 1, alpha: 1)
        label.font = .systemFont(ofSize: 16)
        label.numberOfLines = 0
        label.textAlignment = .center
        label.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(label)
        NSLayoutConstraint.activate([
            label.leadingAnchor.constraint(equalTo: view.layoutMarginsGuide.leadingAnchor),
            label.trailingAnchor.constraint(equalTo: view.layoutMarginsGuide.trailingAnchor),
            label.centerYAnchor.constraint(equalTo: view.centerYAnchor),
        ])
    }

    private func reply(id: Int, result: [String: Any]) {
        let send = { [weak self] in
            guard let self, let web = self.webView else { return }
            guard JSONSerialization.isValidJSONObject(result),
                  let data = try? JSONSerialization.data(withJSONObject: result),
                  var text = String(data: data, encoding: .utf8) else { return }
            text = text.replacingOccurrences(of: "\u{2028}", with: "\\u2028")
            text = text.replacingOccurrences(of: "\u{2029}", with: "\\u2029")
            web.evaluateJavaScript("window.__ladenReply(\(id), \(text))", completionHandler: nil)
        }
        if Thread.isMainThread {
            send()
        } else {
            DispatchQueue.main.async(execute: send)
        }
    }

    @objc private func keyboard(_ note: Notification) {
        guard let frame = note.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? CGRect,
              webView != nil else { return }
        let end = view.convert(frame, from: nil)
        let overlap = max(0, view.bounds.maxY - end.minY)
        bottomPin.constant = -overlap
        let duration = (note.userInfo?[UIResponder.keyboardAnimationDurationUserInfoKey] as? NSNumber)?.doubleValue ?? 0.25
        UIView.animate(withDuration: duration) {
            self.view.layoutIfNeeded()
        }
    }

    private static let void = UIColor(red: 5.0 / 255, green: 7.0 / 255, blue: 10.0 / 255, alpha: 1)

    private static let bridgeSource = """
    (() => {
      const pending = new Map();
      let n = 0;
      window.laden = {
        call(method, params) {
          const id = ++n;
          return new Promise((resolve) => {
            pending.set(id, resolve);
            window.webkit.messageHandlers.host.postMessage(
              JSON.stringify({ id, method, params: params || {} })
            );
          });
        }
      };
      window.__ladenReply = (id, result) => {
        const fn = pending.get(id);
        if (!fn) return;
        pending.delete(id);
        fn(result);
      };
      document.addEventListener("click", (ev) => {
        let node = ev.target;
        while (node && node.tagName !== "A") node = node.parentElement;
        if (!node) return;
        const href = node.href || "";
        if (!/^https?:/i.test(href)) return;
        ev.preventDefault();
        window.laden.call("launch", { command: href });
      }, true);
    })();
    """
}
