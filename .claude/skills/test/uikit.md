# Test patterns for UIKit and non-SwiftData projects

Read by `/test` when `project.ui` is `uikit` or `project.persistence` is not `swiftdata`. Unit and integration tests still use `import Testing`; UI tests stay XCTest.

## Controller tests (mvc) and presenter tests (viper)
Test the logic without a simulator where you can. Inject the repository protocol (or the VIPER interactor protocol) into the controller or presenter and assert on a mock.

```swift
@MainActor
@Test func loadsItemsOnViewDidLoad() throws {
    let repo = Mock<Model>Repository()
    repo.items = [<Model>(...)]
    let vc = <Name>ViewController(repository: repo)
    vc.loadViewIfNeeded()          // runs viewDidLoad without a window
    #expect(vc.tableView.numberOfRows(inSection: 0) == 1)
}
```

- Call `loadViewIfNeeded()` before touching outlets. For storyboard controllers, instantiate with `UIStoryboard(name:bundle:).instantiateViewController(identifier:)`.
- To test an action, call the handler directly (`vc.saveTapped()`) or `button.sendActions(for: .touchUpInside)`.
- A VIPER presenter takes a view protocol and a router protocol. Mock both and assert on the calls.

## In-memory Core Data `[coredata]`
```swift
func makeContainer() -> NSPersistentContainer {
    let container = NSPersistentContainer(name: "<ModelName>")
    let description = NSPersistentStoreDescription()
    description.type = NSInMemoryStoreType
    container.persistentStoreDescriptions = [description]
    container.loadPersistentStores { _, error in
        if let error { fatalError("Store failed to load: \(error)") }
    }
    return container
}
```
Make one container per test so no state leaks between tests.

## In-memory Realm `[realm]`
```swift
let realm = try Realm(configuration: Realm.Configuration(inMemoryIdentifier: UUID().uuidString))
```

## No persistence `[none]`
Mock the network or file-store protocol the same way as the repository mocks above.
