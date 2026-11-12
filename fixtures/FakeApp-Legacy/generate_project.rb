# Regenerates FakeApp-Legacy.xcodeproj (classic groups, UIKit) and the workspace stub.
# Run from this directory: ruby generate_project.rb
require 'xcodeproj'
require 'fileutils'

name = 'FakeApp-Legacy'
FileUtils.rm_rf("#{name}.xcodeproj")
project = Xcodeproj::Project.new("#{name}.xcodeproj")
target = project.new_target(:application, name, :ios, '17.0')
group = project.main_group.new_group(name, name)
target.add_file_references(%w[AppDelegate.swift ViewController.swift].map { |f| group.new_file(f) })
target.build_configurations.each do |c|
  c.build_settings['PRODUCT_BUNDLE_IDENTIFIER'] = 'dev.pragma.fakeapp-legacy'
  c.build_settings['GENERATE_INFOPLIST_FILE'] = 'YES'
  c.build_settings['INFOPLIST_KEY_UIApplicationSupportsIndirectInputEvents'] = 'YES'
  c.build_settings['SWIFT_VERSION'] = '5.0'
  c.build_settings['CODE_SIGNING_ALLOWED'] = 'NO'
end
project.save
project.recreate_user_schemes
FileUtils.rm_rf("#{name}.xcodeproj/xcuserdata")
Xcodeproj::XCScheme.share_scheme(project.path, name)

FileUtils.mkdir_p("#{name}.xcworkspace")
File.write("#{name}.xcworkspace/contents.xcworkspacedata", <<~X)
  <?xml version="1.0" encoding="UTF-8"?>
  <Workspace
     version = "1.0">
     <FileRef
        location = "group:#{name}.xcodeproj">
     </FileRef>
  </Workspace>
X
