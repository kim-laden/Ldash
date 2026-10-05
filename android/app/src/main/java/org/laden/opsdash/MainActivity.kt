package org.laden.opsdash

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.json.JSONObject
import java.io.File

/** Full-screen system WebView. The page is the shared Laden Ops UI. */
class MainActivity : AppCompatActivity(), PhoneHost.PhoneChrome {
    private lateinit var webView: WebView
    private lateinit var host: PhoneHost
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    private val pickFiles = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val callback = fileCallback
        fileCallback = null
        if (callback == null) return@registerForActivityResult
        val data = result.data
        if (result.resultCode != RESULT_OK || data == null) {
            callback.onReceiveValue(null)
            return@registerForActivityResult
        }
        val clip = data.clipData
        val uris = when {
            clip != null && clip.itemCount > 0 -> Array(clip.itemCount) { clip.getItemAt(it).uri }
            data.data != null -> arrayOf(data.data!!)
            else -> null
        }
        callback.onReceiveValue(uris)
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        host = PhoneHost(this, this)
        webView = WebView(this)
        setContentView(webView)
        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.allowFileAccess = true
        @Suppress("DEPRECATION")
        settings.allowFileAccessFromFileURLs = true
        webView.setBackgroundColor(0xFF05070A.toInt())
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val uri = request.url ?: return true
                val scheme = uri.scheme?.lowercase() ?: return true
                if (scheme == "http" || scheme == "https") {
                    openWeb(uri)
                    return true
                }
                return scheme != "file" && scheme != "about"
            }

            override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                view?.evaluateJavascript(BRIDGE, null)
            }
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView?,
                callback: ValueCallback<Array<Uri>>?,
                params: FileChooserParams?,
            ): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                val intent = Intent(Intent.ACTION_GET_CONTENT).apply {
                    addCategory(Intent.CATEGORY_OPENABLE)
                    type = "*/*"
                    putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                }
                pickFiles.launch(Intent.createChooser(intent, "Import"))
                return true
            }
        }
        webView.addJavascriptInterface(Bridge(), "Laden")
        if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
            WebViewCompat.addDocumentStartJavaScript(webView, BRIDGE, setOf("*"))
        }
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                moveTaskToBack(true)
            }
        })
        webView.loadUrl("file:///android_asset/web/index.html")
    }

    fun onBridge(raw: String) {
        val objectJson = try {
            JSONObject(raw)
        } catch (_: Exception) {
            return
        }
        val id = objectJson.optInt("id", 0)
        val method = objectJson.optString("method", "")
        val params = objectJson.optJSONObject("params") ?: JSONObject()
        host.dispatch(method, params) { result -> reply(id, result) }
    }

    private fun reply(id: Int, result: JSONObject) {
        val text = result.toString().replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        val script = "window.__ladenReply($id, $text)"
        webView.post { webView.evaluateJavascript(script, null) }
    }

    override fun presentFile(file: File) {
        val show = Runnable {
            val uri = uriFor(file)
            val view = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, if (file.name.endsWith(".pdf")) "application/pdf" else "application/octet-stream")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            try {
                startActivity(Intent.createChooser(view, file.name))
            } catch (_: Exception) {
                // The file is already stored. A missing viewer is not a vault error.
            }
        }
        runOnUiThread(show)
    }

    override fun openWeb(uri: Uri) {
        runOnUiThread {
            try {
                startActivity(Intent(Intent.ACTION_VIEW, uri))
            } catch (_: Exception) {
            }
        }
    }

    override fun shareFile(file: File) {
        runOnUiThread {
            val uri = uriFor(file)
            val send = Intent(Intent.ACTION_SEND).apply {
                type = "text/plain"
                putExtra(Intent.EXTRA_STREAM, uri)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                clipData = android.content.ClipData.newRawUri(file.name, uri)
            }
            try {
                startActivity(Intent.createChooser(send, file.name))
            } catch (_: Exception) {
            }
        }
    }

    private fun uriFor(file: File): Uri {
        return FileProvider.getUriForFile(this, "org.laden.opsdash.files", file)
    }

    private inner class Bridge {
        @android.webkit.JavascriptInterface
        fun post(raw: String) {
            onBridge(raw)
        }
    }

    companion object {
        private val BRIDGE = """
            (() => {
              if (window.laden) return;
              const pending = new Map();
              let n = 0;
              window.laden = {
                call(method, params) {
                  const id = ++n;
                  return new Promise((resolve) => {
                    pending.set(id, resolve);
                    Laden.post(JSON.stringify({ id, method, params: params || {} }));
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
        """.trimIndent()
    }
}
