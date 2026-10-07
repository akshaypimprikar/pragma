#!/usr/bin/env ruby
# Adds new .swift files to a classic-group Xcode project's Sources build phase with
# the xcodeproj gem (a CocoaPods dependency, so any CocoaPods project has it).
#
# Usage: register_files.rb [--check] PROJECT_DIR APP_NAME FILE...
#   (default)  register each FILE; already-registered files are left alone
#   --check    register nothing; list unregistered FILEs and exit 1 if any
# Exit codes: 0 ok, 1 --check found unregistered files, 3 stop and ask the human
#             (stderr says why), 2 bad arguments.
require 'open3'
require 'pathname'

check = ARGV.first == '--check'
ARGV.shift if check
dir, app, *files = ARGV
abort 'usage: register_files.rb [--check] PROJECT_DIR APP_NAME FILE...' if dir.nil? || app.nil? || files.empty?

begin
  require 'xcodeproj'
rescue LoadError
  warn 'xcodeproj gem not installed (gem install xcodeproj; on Xcode 27 set SDKROOT to an older SDK if the build fails). Add the files in Xcode, then re-run.'
  exit 3
end

mode, reason, = Open3.capture3(File.join(__dir__, 'detect_file_registration.sh'), dir, app)
mode = mode.strip
unless mode == 'classic'
  warn(mode == 'synchronized' ? 'project uses synchronized groups: new files compile without registration.' : reason.to_s.strip)
  warn "mode is #{mode}: stop and ask the human." unless mode == 'synchronized'
  exit 3
end

project = Xcodeproj::Project.open(File.join(dir, "#{app}.xcodeproj"))
root = Pathname.new(File.expand_path(dir))

registered = lambda do |path|
  project.targets.any? do |t|
    t.source_build_phase.files.any? { |bf| bf.file_ref && bf.file_ref.real_path.to_s == path }
  end
end

todo = files.map { |f| File.expand_path(f) }.reject { |f| registered.call(f) }
if check
  todo.each { |f| puts f }
  exit(todo.empty? ? 0 : 1)
end

target = project.targets.find { |t| t.name == app } || project.targets.first
todo.each do |abs|
  group = project.main_group
  Pathname.new(abs).relative_path_from(root).dirname.each_filename do |name|
    next if name == '.'
    group = group.children.find { |c| c.isa == 'PBXGroup' && (c.path == name || c.name == name) } ||
            group.new_group(name, name)
  end
  target.add_file_references([group.new_file(abs)])
end
project.save unless todo.empty?
