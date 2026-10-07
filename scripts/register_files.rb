#!/usr/bin/env ruby
# Adds new .swift files to an Xcode project's Sources build phase when the project
# does not use synchronized groups. Classic projects (project.pbxproj) are edited with
# the xcodeproj gem (a CocoaPods dependency, so any CocoaPods project has it);
# project.xcproj (Xcode 27.2 beta) is relaxed JSON and is edited as text.
#
# Usage: register_files.rb [--check] PROJECT_DIR APP_NAME FILE...
#   (default)  register each FILE; already-registered files are left alone
#   --check    register nothing; list unregistered FILEs and exit 1 if any
# A FILE under a directory named like a target (AppTests/...) goes to that target,
# otherwise to the APP_NAME target.
# Exit codes: 0 ok, 1 --check found unregistered files or the project is unparseable,
#             3 stop and ask the human (stderr says why), 2 bad arguments.
require 'json'
require 'open3'
require 'pathname'

check = ARGV.first == '--check'
ARGV.shift if check
dir, app, *files = ARGV
abort 'usage: register_files.rb [--check] PROJECT_DIR APP_NAME FILE...' if dir.nil? || app.nil? || files.empty?

mode, reason, = Open3.capture3(File.join(__dir__, 'detect_file_registration.sh'), dir, app)
mode = mode.strip
root = Pathname.new(File.expand_path(dir))
files = files.map { |f| File.expand_path(f) }.select { |f| f.end_with?('.swift') }
if files.empty?
  warn 'no .swift files given: nothing to register.'
  exit 0
end
outside = files.reject { |f| Pathname.new(f).relative_path_from(root).each_filename.first != '..' }
unless outside.empty?
  warn "outside the project directory (#{root}): #{outside.join(', ')}. Stop and ask the human."
  exit 3
end

unless %w[classic xcproj].include?(mode)
  warn(mode == 'synchronized' ? 'project uses synchronized groups: new files compile without registration.' : reason.to_s.strip)
  warn "mode is #{mode}: stop and ask the human." unless mode == 'synchronized'
  exit 3
end

def target_for(abs, root, app, names)
  first = Pathname.new(abs).relative_path_from(root).each_filename.first
  names.include?(first) ? first : app
end

# Relaxed JSON (trailing commas, // comments) with source offsets, so an edit keeps
# the rest of the file byte for byte.
class Xcproj
  class ParseError < StandardError; end
  Node = Struct.new(:type, :s, :e, :value, :pairs, :items)

  attr_reader :text

  def initialize(text)
    @text = text
    reparse
  end

  def reparse
    @i = 0
    @root = value
    skip
    raise ParseError, "unexpected text at offset #{@i}" unless @i == @text.length
    raise ParseError, 'top level is not an object' unless @root.type == :obj
  end

  def skip
    loop do
      if @text[@i] =~ /\s/ then @i += 1
      elsif @text[@i, 2] == '//' then @i += 1 until @i >= @text.length || @text[@i] == "\n"
      else break
      end
    end
  end

  def value
    skip
    c = @text[@i]
    case c
    when '{' then container(:obj, '}')
    when '[' then container(:arr, ']')
    when '"'
      s = @i
      @i += 1
      @i += (@text[@i] == '\\' ? 2 : 1) while @i < @text.length && @text[@i] != '"'
      raise ParseError, 'unterminated string' if @i >= @text.length
      @i += 1
      Node.new(:str, s, @i, JSON.parse(@text[s...@i]))
    when nil then raise ParseError, 'unexpected end of file'
    else
      s = @i
      @i += 1 while @i < @text.length && @text[@i] !~ /[\s,\]}]/
      raise ParseError, "unexpected character #{c.inspect} at offset #{s}" if @i == s
      Node.new(:lit, s, @i, @text[s...@i])
    end
  end

  def container(type, close)
    node = Node.new(type, @i, nil, nil, [], [])
    @i += 1
    loop do
      skip
      if @text[@i] == close
        @i += 1
        break
      end
      if type == :obj
        key = value
        raise ParseError, "object key expected at offset #{key.s}" unless key.type == :str
        skip
        raise ParseError, "':' expected at offset #{@i}" unless @text[@i] == ':'
        @i += 1
        node.pairs << [key.value, value]
      else
        node.items << value
      end
      skip
      @i += 1 if @text[@i] == ','
    end
    node.e = @i
    node
  end

  def get(obj, key)
    pair = obj.pairs.find { |k, _| k == key }
    pair && pair[1]
  end

  def str(obj, key)
    n = get(obj, key)
    n && n.type == :str ? n.value : nil
  end

  def files_array
    n = get(@root, 'files')
    raise ParseError, 'no "files" array' unless n && n.type == :arr
    n
  end

  def target_names
    n = get(@root, 'targets')
    n ? n.items.map { |t| str(t, 'name') }.compact : []
  end

  def group?(item)
    item.type == :obj && str(item, 'kind') == 'group'
  end

  # Absolute paths of every file that is in some target's compile-sources phase.
  def registered(root, items = files_array.items, base = root, out = [])
    items.each do |item|
      next unless item.type == :obj
      path = str(item, 'path')
      if group?(item)
        kids = get(item, 'children')
        sub = path && !path.start_with?('<') ? base + path : base
        registered(root, kids.items, sub, out) if kids
      elsif path && !path.start_with?('<')
        members = get(item, 'target-membership')
        phases = members ? members.items.map(&:value) : []
        out << (base + path).to_s if phases.any? { |m| m.to_s.end_with?('/compile-sources') }
      end
    end
    out
  end

  # Inserts `entry` (a block of text lines) before the closing bracket of `arr`.
  def append(arr, entry)
    close = arr.e - 1
    last = @text.rindex(/\S/, close - 1)
    if last && @text[last] !~ /[\[,]/ # the previous item lacks its comma
      @text.insert(last + 1, ',')
      close += 1
    end
    line_start = @text.rindex("\n", close - 1)
    if line_start.nil? || @text[line_start + 1...close] !~ /\A\s*\z/
      raise ParseError, 'array is not laid out one item per line'
    end
    indent = @text[line_start + 1...close]
    block = entry.map { |l| "#{indent}  #{l}\n" }.join
    @text = @text[0..line_start] + block + @text[line_start + 1..-1]
    reparse
  end

  # The group in `arr` whose path or name covers the front of `comps` (a group path
  # may have several components, "Sources/App"), with how many components it covers.
  def find_group(arr, comps)
    best = [nil, 0]
    arr.items.each do |it|
      next unless group?(it)
      [str(it, 'path'), str(it, 'name')].compact.each do |key|
        parts = key.split('/')
        best = [it, parts.length] if comps.first(parts.length) == parts && parts.length > best[1]
      end
    end
    best
  end

  # The deepest children array reached by walking `comps` from the top, and how many
  # components that consumed.
  def walk(comps)
    arr = files_array
    used = 0
    while used < comps.length
      group, n = find_group(arr, comps[used..-1])
      break unless group
      arr = get(group, 'children')
      used += n
    end
    [arr, used]
  end

  def add_file(abs, root, target)
    rel = Pathname.new(abs).relative_path_from(root)
    comps = rel.dirname.each_filename.reject { |c| c == '.' }
    loop do
      arr, used = walk(comps)
      break if used == comps.length
      append(arr, ['{', '  "kind": "group",', "  \"path\": #{comps[used].to_json},", '  "children": [', '  ],', '},'])
    end
    append(walk(comps).first, ["{ \"path\": #{rel.basename.to_s.to_json}, \"index\": true, \"target-membership\": [ #{(target + '/compile-sources').to_json} ] },"])
  end
end

if mode == 'xcproj'
  path = File.join(dir, "#{app}.xcodeproj", 'project.xcproj')
  begin
    proj = Xcproj.new(File.read(path))
    done = proj.registered(root)
    todo = files.reject { |f| done.include?(f) }
    if check
      todo.each { |f| puts f }
      exit(todo.empty? ? 0 : 1)
    end
    plan = todo.map { |abs| [abs, target_for(abs, root, app, proj.target_names)] }
    missing = plan.map(&:last).uniq - proj.target_names
    unless missing.empty?
      warn "no target named #{missing.join(', ')} in #{path}: pass the app target's name as APP_NAME. Stop and ask the human."
      exit 3
    end
    plan.each { |abs, target| proj.add_file(abs, root, target) }
    File.write(path, proj.text) unless todo.empty?
  rescue Xcproj::ParseError => e
    warn "cannot use #{path}: #{e.message}"
    exit(check ? 1 : 3)
  end
  exit 0
end

begin
  require 'xcodeproj'
rescue LoadError
  warn 'xcodeproj gem not installed (gem install xcodeproj; on Xcode 27 set SDKROOT to an older SDK if the build fails). Add the files in Xcode, then re-run.'
  exit 3
end

project = Xcodeproj::Project.open(File.join(dir, "#{app}.xcodeproj"))

registered = lambda do |path|
  project.targets.any? do |t|
    t.source_build_phase.files.any? { |bf| bf.file_ref && bf.file_ref.real_path.to_s == path }
  end
end

todo = files.reject { |f| registered.call(f) }
if check
  todo.each { |f| puts f }
  exit(todo.empty? ? 0 : 1)
end

names = project.targets.map(&:name)
plan = todo.map { |abs| [abs, target_for(abs, root, app, names)] }
missing = plan.map(&:last).uniq - names
unless missing.empty?
  warn "no target named #{missing.join(', ')} in #{app}.xcodeproj: pass the app target's name as APP_NAME. Stop and ask the human."
  exit 3
end
plan.each do |abs, name|
  target = project.targets.find { |t| t.name == name }
  comps = Pathname.new(abs).relative_path_from(root).dirname.each_filename.reject { |c| c == '.' }
  group = project.main_group
  i = 0
  while i < comps.length
    found = nil
    used = 0
    group.children.each do |c|
      next unless c.isa == 'PBXGroup'
      [c.path, c.name].compact.each do |key|
        parts = key.split('/')
        if comps[i, parts.length] == parts && parts.length > used
          found = c
          used = parts.length
        end
      end
    end
    if found
      group = found
      i += used
    else
      group = group.new_group(comps[i], comps[i])
      i += 1
    end
  end
  target.add_file_references([group.new_file(abs)])
end
project.save unless todo.empty?
