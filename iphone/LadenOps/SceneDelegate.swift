import UIKit

final class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?

    func scene(
        _ scene: UIScene,
        willConnectTo session: UISceneSession,
        options connectionOptions: UIScene.ConnectionOptions
    ) {
        guard let windowScene = scene as? UIWindowScene else { return }
        let window = UIWindow(windowScene: windowScene)
        window.backgroundColor = UIColor(red: 5.0 / 255, green: 7.0 / 255, blue: 10.0 / 255, alpha: 1)
        window.rootViewController = ShellViewController()
        window.makeKeyAndVisible()
        self.window = window
    }
}
