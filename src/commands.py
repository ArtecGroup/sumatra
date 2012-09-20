"""
Commands provided by the smt tool.

Each command corresponds to a function in this module.
"""

import os.path
import sys
from optparse import OptionParser
from textwrap import dedent
from copy import deepcopy
import warnings
import re

from sumatra.programs import get_executable
from sumatra.datastore import FileSystemDataStore, ArchivingFileSystemDataStore
from sumatra.projects import Project, load_project
from sumatra.launch import SerialLaunchMode, DistributedLaunchMode
from sumatra.parameters import build_parameters
from sumatra.recordstore import get_record_store
from sumatra.versioncontrol import get_working_copy, get_repository, UncommittedModificationsError
from sumatra.formatting import get_diff_formatter
from sumatra.records import MissingInformationError


def parse_executable_str(exec_str):
    """
    Split the string describing the executable into a path part and an
    options part.
    """
    first_space = exec_str.find(" ")
    if first_space == -1: first_space = len(exec_str)
    return exec_str[:first_space], exec_str[first_space:]


list_pattern = re.compile(r'^\s*\[.*\]\s*$')
tuple_pattern = re.compile(r'^\s*\(.*\)\s*$')

def parse_command_line_parameter(p):
    pos = p.find('=')
    if pos == -1:
        raise Exception("Not a valid command line parameter. String must be of form 'name=value'")
    name = p[:pos]
    value = p[pos+1:]
    if list_pattern.match(value) or tuple_pattern.match(value):
        value = eval(value)
    else:
        for cast in int, float:
            try:
                value = cast(value)
                break
            except ValueError:
                pass
    return {name: value}

def parse_arguments(args, input_datastore):  
    cmdline_parameters = {}
    script_args = []
    parameter_sets = []
    input_data = []
    for arg in args:
        have_parameters = False
        if os.path.exists(arg): # could be a parameter file or a data file
            try:
               parameter_sets.append(build_parameters(arg))
               script_args.append("<parameters>")
               have_parameters = True
            except SyntaxError:
               pass
        if not have_parameters:
            if input_datastore.contains_path(arg):
                data_key = input_datastore.generate_keys(arg)
                input_data.extend(data_key)
                script_args.append(arg)
            elif "=" in arg: # cmdline parameter 
                cmdline_parameters.update(parse_command_line_parameter(arg))
            else: # a flag or something, passed on unchanged
                script_args.append(arg)
    assert len(parameter_sets) < 2, "No more than one parameter file may be supplied." # temporary restriction
    if cmdline_parameters:
        if parameter_sets:
            parameter_sets[0].update(cmdline_parameters)
        else:
            raise Exception("Command-line parameters supplied but without a parameter file to put them into.")
            # ought really to have a more specific Exception and to catch it so as to give a helpful error message to user
    return parameter_sets, input_data, " ".join(script_args)


def init(argv):
    """Create a new project in the current directory."""
    usage = "%prog init [options] NAME"
    description = "Create a new project called NAME in the current directory."
    parser = OptionParser(usage=usage,
                          description=description)
    parser.add_option('-d', '--datapath', metavar='PATH', default='./Data', help="set the path to the directory in which smt will search for output datafiles generated by the simulation/analysis. Defaults to %default")
    parser.add_option('-i', '--input', metavar='PATH', default='/', help="set the path to the directory relative to which input datafile paths will be given. Defaults to the filesystem root.")
    parser.add_option('-l', '--addlabel', choices=['cmdline', 'parameters', None], metavar='OPTION',
                      default=None, help="If this option is set, smt will append the record label either to the command line (option 'cmdline') or to the parameter file (option 'parameters'), and will add the label to the datapath when searching for datafiles. It is up to the user to make use of this label inside their program to ensure files are created in the appropriate location.")
    parser.add_option('-e', '--executable', metavar='PATH', help="set the path to the executable. If this is not set, smt will try to infer the executable from the value of the --main option, if supplied, and will try to find the executable from the PATH environment variable, then by searching various likely locations on the filesystem.")
    parser.add_option('-r', '--repository', help="the URL of a Subversion or Mercurial repository containing the code. This will be checked out/cloned into the current directory.")
    parser.add_option('-m', '--main', help="the name of the script that would be supplied on the command line if running the simulation or analysis normally, e.g. init.hoc.")
    parser.add_option('-c', '--on-changed', default='error', help="the action to take if the code in the repository or any of the depdendencies has changed. Defaults to %default") # need to add list of allowed values
    parser.add_option('-s', '--store', help="specify the path to the record store, either an existing one or one to be created.")
    parser.add_option('-D', '--debug', action='store_true', help="print debugging information.")
    parser.add_option('-A', '--archive', metavar='PATH', help="specify a directory in which to archive output datafiles. If not specified, datafiles are not archived.")
    
    (options, args) = parser.parse_args(argv)
    
    try:
        project = load_project()
        parser.error("A project already exists in this directory.")
    except Exception:
        pass
    
    if len(args) != 1:
        parser.error('You must supply a name.')
    project_name = args[0]
    
    global _debug
    _debug = options.debug

    if not os.path.exists(".smt"):
        os.mkdir(".smt")

    if options.repository:
        repository = get_repository(options.repository)
        repository.checkout()
    else:
        repository = get_working_copy().repository # if no repository is specified, we assume there is a working copy in the current directory.        

    if options.executable:
        executable_path, executable_options = parse_executable_str(options.executable)
        executable = get_executable(path=executable_path)
        executable.options = executable_options
    elif options.main:
        try:
            executable = get_executable(script_file=options.main)
        except Exception: # assume unrecognized extension - really need more specific exception type
            # should warn that extension unrecognized
            executable = None
    else:
        executable = None
    if options.store:
        record_store = get_record_store(options.store)
    else:
        record_store = 'default'
    
    if options.archive and options.archive.lower() != 'false':
        if options.archive.lower() == "true":
            options.archive = ".smt/archive"
        output_datastore = ArchivingFileSystemDataStore(options.datapath, options.archive)
    else:
        output_datastore = FileSystemDataStore(options.datapath)
    input_datastore = FileSystemDataStore(options.input)

    project = Project(name=project_name,
                      default_executable=executable,
                      default_repository=repository,
                      default_main_file=options.main, # what if incompatible with executable?
                      default_launch_mode=SerialLaunchMode(),
                      data_store=output_datastore,
                      record_store=record_store,
                      on_changed=options.on_changed,
                      data_label=options.addlabel,
                      input_datastore=input_datastore)
    project.save()

def configure(argv):
    """Modify the settings for the current project."""
    usage = "%prog configure [options]"
    description = __doc__
    parser = OptionParser(usage=usage,
                          description=description)
    parser.add_option('-d', '--datapath', metavar='PATH', help="set the path to the directory in which smt will search for datafiles generated by the simulation or analysis. Defaults to %default")
    parser.add_option('-i', '--input', metavar='PATH', default=None, help="set the path to the directory in which smt will search for input datafiles.")
    parser.add_option('-l', '--addlabel', choices=['cmdline', 'parameters', None], metavar='OPTION',
                      default=None, help="If this option is set, smt will append the record label either to the command line (option 'cmdline') or to the parameter file (option 'parameters'), and will add the label to the datapath when searching for datafiles. It is up to the user to make use of this label inside their program to ensure files are created in the appropriate location.")
    parser.add_option('-e', '--executable', metavar='PATH', help="set the path to the executable.")
    parser.add_option('-r', '--repository', help="the URL of a Subversion or Mercurial repository containing the code. This will be checked out/cloned into the current directory.")
    parser.add_option('-m', '--main', help="the name of the script that would be supplied on the command line if running the simulator normally, e.g. init.hoc.")
    parser.add_option('-c', '--on-changed', help="may be 'store-diff' or 'error': the action to take if the code in the repository or any of the dependencies has changed. Defaults to 'error'", choices=['store-diff', 'error'])
    parser.add_option('-A', '--archive', metavar='PATH', help="specify a directory in which to archive output datafiles. If not specified, or if 'false', datafiles are not archived.")
 
    (options, args) = parser.parse_args(argv)
    if len(args) != 0:
        parser.error('configure does not take any arguments')
    project = load_project()
    if options.archive:
        if options.archive.lower() == "true":
            options.archive = ".smt/archive"
        if hasattr(project.data_store, 'archive_store'): # current data store is archiving
            if options.archive.lower() == 'false':
                project.data_store = FileSystemDataStore(project.data_store.root)
            else:
                project.data_store.archive_store = options.archive
        else: # current data store is not archiving
            if options.archive.lower() != 'false':
                project.data_store = ArchivingFileSystemDataStore(options.datapath, options.archive)
    if options.datapath:
        project.data_store.root = options.datapath
    if options.input:
        project.input_datastore.root = options.input
    if options.repository:
        repository = get_repository(options.repository)
        repository.checkout()
        project.default_repository = repository
    if options.main:
        project.default_main_file = options.main
    if options.executable:
        executable_path, executable_options = parse_executable_str(options.executable)
        project.default_executable = get_executable(executable_path,
                                                    script_file=options.main or project.default_main_file)
        project.default_executable.options = executable_options
    if options.on_changed:
        project.on_changed = options.on_changed
    if options.addlabel:
        project.data_label = options.addlabel
    project.save()


def info(argv):
    """Print information about the current project."""
    usage = "%prog info"
    description = """Print information about the current project."""
    parser = OptionParser(usage=usage,
                          description=description)
    (options, args) = parser.parse_args(argv)
    if len(args) != 0:
        parser.error('info does not take any arguments')
    try:
        project = load_project()
    except IOError, err:
        print err
        sys.exit(1)
    print project.info()


def run(argv):
    """Run a simulation or analysis."""
    usage = "%prog run [options] [arg1, ...] [param=value, ...]"
    description = dedent("""\
      The list of arguments will be passed on to the simulation/analysis script.
      It should normally contain at least the name of a parameter file, but
      can also contain input files, flags, etc.
      
      If the parameter file should be in a format that Sumatra understands (see
      documentation), then the parameters will be stored to allow future
      searching, comparison, etc. of records.
      
      For convenience, it is possible to specify a file with default parameters
      and then specify those parameters that are different from the default values
      on the command line with any number of param=value pairs (note no space
      around the equals sign).""")
    parser = OptionParser(usage=usage,
                          description=description)
    parser.add_option('-v', '--version', metavar='REV',
                      help="use version REV of the code (if this is not the same as the working copy, it will be checked out of the repository). If this option is not specified, the most recent version in the repository will be used. If there are changes in the working copy, the user will be prompted to commit them first")
    parser.add_option('-l', '--label', help="specify a label for the experiment. If no label is specified, the label will be based on PARAMFILE and the timestamp.")
    parser.add_option('-r', '--reason', help="explain the reason for running this simulation/analysis.")
    parser.add_option('-e', '--executable', metavar='PATH', help="Use this executable for this run. If not specified, the project's default executable will be used.")
    parser.add_option('-m', '--main', help="the name of the script that would be supplied on the command line if running the simulation/analysis normally, e.g. init.hoc. If not specified, the project's default will be used.")
    parser.add_option('-n', '--num_processes', metavar='N', type="int",
                      help="run a distributed computation on N processes using MPI. If this option is not used, or if N=0, a normal, serial simulation/analysis is run.")
    parser.add_option('-t', '--tag', help="tag you want to add to the project")
    
    (options, args) = parser.parse_args(argv)
    
    project = load_project()
    # import pdb; pdb.set_trace()
    parameters, input_data, script_args = parse_arguments(args, project.input_datastore)
    if len(parameters) == 0:
        parameters = {}
    elif len(parameters) == 1:
        parameters = parameters[0]
    else:
        parser.error("Only a single parameter file allowed.") # for now

    if options.executable:
        executable_path, executable_options = parse_executable_str(options.executable)
        executable = get_executable(path=executable_path)
        executable.options = executable_options
    elif options.main:
        executable = get_executable(script_file=options.main) # should we take the options from project.default_executable, if they match?
    else:
        executable = 'default'
    if options.num_processes:
        launch_mode = DistributedLaunchMode(n=options.num_processes)
    else:
        launch_mode = SerialLaunchMode()
    reason = options.reason or ''
    if reason:
        reason = reason.strip('\'"')
    
    label = options.label
    try:
        run_label = project.launch(parameters, input_data, script_args,
                                   label=label, reason=reason,
                                   executable=executable,
                                   main_file=options.main or 'default',
                                   version=options.version or 'latest',
                                   launch_mode=launch_mode)
    except (UncommittedModificationsError, MissingInformationError), err:
        print err
        sys.exit(1)
    if options.tag:
        project.add_tag(run_label, options.tag)


def list(argv):
    """List records belonging to the current project."""
    usage = "%prog list [options] [TAGS]"
    description = dedent("""\
      If TAGS (optional) is specified, then only records with a tag in TAGS
      will be listed.""")
    parser = OptionParser(usage=usage,
                          description=description)
    parser.add_option('-l', '--long', action="store_const", const="long",
                      dest="mode", default="short",
                      help="prints full information for each record"),
    parser.add_option('-T', '--table', action="store_const", const="table",
                      dest="mode", help="prints information in tab-separated columns")
    parser.add_option('-f', '--format', metavar='FMT', choices=['text', 'html'], default='text',
                      help="FMT can be 'text' (default) or 'html'.")
    (options, args) = parser.parse_args(argv)
    tags = args
    
    project = load_project()
    print project.format_records(tags=tags, mode=options.mode, format=options.format)

def delete(argv):
    """Delete records or records with a particular tag from a project."""
    usage = "%prog delete [options] LIST"
    description = dedent("""\
      LIST should be a space-separated list of labels for individual records or
      of tags. If it contains tags, you must set the --tag/-t option (see below).
      The special value "last" allows you to delete the most recent simulation/analysis.
      If you want to delete all records, just delete the .smt directory and use
      smt init to create a new, empty project.""")
    parser = OptionParser(usage=usage,
                          description=description)
    parser.add_option('-t', '--tag', action='store_true',
                      help="interpret LIST as containing tags. Records with any of these tags will be deleted.")
    parser.add_option('-d', '--data', action='store_true',
                      help="also delete any data associated with the record(s).")
                      
    (options, args) = parser.parse_args(argv)
    if len(args) < 1:
        parser.error('Please specify a record or list of records to be deleted.')
        
    project = load_project()
    if options.tag:
        for tag in args:
            n = project.delete_by_tag(tag, delete_data=options.data)
            print n, "records deleted."
    else:
        for label in args:
            if label == 'last':
                label = project.most_recent().label
            try:
                project.delete_record(label, delete_data=options.data)
            except Exception: # could be KeyError or DoesNotExist: should create standard NoSuchRecord or RecordDoesNotExist exception
                warnings.warn("Could not delete record '%s' because it does not exist" % label)
            
def comment(argv):
    """Add a comment to an existing record."""
    usage = "%prog comment [options] [LABEL] [COMMENT]"
    description = dedent("""\
      This command is used to describe the outcome of the simulation/analysis. If LABEL
      is omitted, the comment will be added to the most recent experiment.
      If the '-f/--file' option is set, COMMENT should be the name of a file
      containing the comment, otherwise it should be a string of text.""")
    parser = OptionParser(usage=usage,
                          description=description)
    parser.add_option('-r', '--replace', action='store_true',
                      help="if this flag is set, any existing comment will be overwritten, otherwise, the new comment will be appended to the end, starting on a new line")
    parser.add_option('-f', '--file', action='store_true',
                      help="interpret COMMENT as the path to a file containing the comment")
    (options, args) = parser.parse_args(argv)
    if len(args) == 1:
        label = None
        comment = args[0]
    elif len(args) == 2:
        label, comment = args
    else:
        parser.error('Please provide a comment.')
    if options.file:
        f = open(comment, 'r')
        comment = f.read()
        f.close()
        
    project = load_project()
    label = label or project.most_recent().label
    project.add_comment(label, comment)
    
def tag(argv):
    """Tag, or remove a tag, from a record or records."""
    usage = "%prog tag [options] TAG [LIST]"
    description = dedent("""\
      If TAG contains spaces, it must be enclosed in quotes. LIST should be a
      space-separated list of labels for individual records. If it is omitted,
      only the most recent record will be tagged. If the '-d/--delete' option
      is set, the tag will be removed from the records.""")
    parser = OptionParser(usage=usage,
                          description=description)
    parser.add_option('-r', '--remove', action='store_true',
                      help="remove the tag from the record(s), rather than adding it.")
    (options, args) = parser.parse_args(argv)
    if len(args) > 0:
        tag = args[0]
        project = load_project()
        if options.remove:
            op = project.remove_tag
        else:
            op = project.add_tag
        if len(args) > 1:
            labels = args[1:]
        else:
            labels = [project.most_recent().label]
        for label in labels:
            op(label, tag)
    else:
        parser.error('Please provide a tag.')

def repeat(argv):
    """Re-run a previous simulation or analysis."""
    usage = "%prog repeat LABEL"
    description = dedent("""\
        Re-run a previous simulation/analysis under (in theory) identical
        conditions, and check that the results are unchanged.""")
    parser = OptionParser(usage=usage,
                          description=description)
    (options, args) = parser.parse_args(argv)
    if len(args) != 1:
        parser.error('One and only one label should be specified.')
    else:
        original_label = args[0]
    project = load_project()
    if original_label == 'last':
        tmp = project.most_recent()
    else:
        tmp = project.get_record(original_label)
    original = deepcopy(tmp)
    if hasattr(tmp.parameters, '_url'): # for some reason, _url is not copied.
        original.parameters._url = tmp.parameters._url # this is a hackish solution - needs fixed properly
    original.repository.checkout() # should do nothing if there is already a checkout
    new_label = project.launch(parameters=original.parameters,
                               input_data=original.input_data,
                               script_args=original.script_arguments,
                               executable=original.executable,
                               main_file=original.main_file,
                               repository=original.repository,
                               version=original.version,
                               launch_mode=original.launch_mode,
                               label="%s_repeat" % original.label,
                               reason="Repeat experiment %s" % original.label)
    diff = project.compare(original.label, new_label)
    if diff:
        formatter = get_diff_formatter()(diff)
        msg = ["The new record does not match the original. It differs as follows.",
               formatter.format('short'),
               "run smt diff --long %s %s to see the differences in detail." % (original.label, new_label)]
        msg = "\n".join(msg)
    else:
        msg = "The new record exactly matches the original."
    print msg
    project.add_comment(new_label, msg)    

def diff(argv):
    """Show the differences, if any, between two records."""
    usage = "%prog diff [options] LABEL1 LABEL2"
    description = dedent("""Show the differences, if any, between two records.""")
    parser = OptionParser(usage=usage,
                          description=description)
    parser.add_option('-i', '--ignore', action="append",
                      help="a regular expression pattern for filenames to ignore when evaluating differences in output data. To supply multiple patterns, use the -i option multiple times.")
    parser.add_option('-l', '--long', action="store_const", const="long",
                      dest="mode", default="short",
                      help="prints full information for each record"),
    (options, args) = parser.parse_args(argv)
    if len(args) != 2:
        parser.error('Please specify two labels.')
    label1, label2 = args
    if options.ignore is None:
        options.ignore = []
    
    project = load_project()
    print project.show_diff(label1, label2, mode=options.mode, ignore_filenames=options.ignore)
    
def help(argv):
    usage = "%prog help [CMD]"
    description = dedent("""Get help on an %prog command.""")
    parser = OptionParser(usage=usage,
                          description=description)
    (options, args) = parser.parse_args(argv)
    if len(args) != 1:
        parser.error('Please specify a command on which you would like help.')
    cmd = args[0]
    try:
        func = globals()[cmd]
        func(['--help'])
    except KeyError:
        parser.error('"%s" is not an smt command.' % cmd)
    
def upgrade(argv):
    usage = "%prog upgrade"
    description = dedent("""\
        Upgrade an existing Sumatra project. You must have previously run
        "smt export" or the standalone 'export.py' script.""")
    parser = OptionParser(usage=usage,
                          description=description)
    (options, args) = parser.parse_args(argv)
    if len(args) != 0:
        parser.error('%prog upgrade does not take any arguments.')
    
    import shutil
    from datetime import datetime
    backup_dir = ".smt_backup_%s" % datetime.now().strftime("%Y%m%d%H%M%S")
    shutil.move(".smt", backup_dir)
    # upgrade the project data
    os.mkdir(".smt")
    shutil.copy("%s/project_export.json" % backup_dir, ".smt/project")
    project = load_project()
    project.save()
    # upgrade the record store
    filename = "%s/records_export.json" % backup_dir
    if os.path.exists(filename):
        f = open(filename)
        project.record_store.import_(project.name, f.read())
        f.close()
    else:
        print "Record file not found"
        sys.exit(1)

def export(argv):
    usage = "%prog export"
    description = dedent("""\
        Export a Sumatra project and its records to JSON. This is needed before running upgrade.""")
    parser = OptionParser(usage=usage,
                          description=description)
    (options, args) = parser.parse_args(argv)
    if len(args) != 0:
        parser.error('%prog export does not take any arguments.')
    # copy the project data
    import shutil
    shutil.copy(".smt/project", ".smt/project_export.json")
    # export the record data
    project = load_project()
    f = open(".smt/records_export.json", 'w')
    f.write(project.record_store.export(project.name))
    f.close()

def sync(argv):
    usage = "%prog sync PATH1 [PATH2]"
    description = dedent("""\
        Synchronize two record stores. If both PATH1 and PATH2 are given, the
        record stores at those locations will be synchronized. If only PATH1 is
        given, and the command is run in a directory containing a Sumatra
        project, only that project's records be synchronized with the store at
        PATH1. Note that PATH1 and PATH2 may be either filesystem paths or URLs.
        """) # need to say what happens if the sync is incomplete due to label collisions
    parser = OptionParser(usage=usage,
                          description=description)
    (options, args) = parser.parse_args(argv)
    
    if len(args) == 2:
        store1 = get_record_store(args[0])
        store2 = get_record_store(args[1])
        collisions = store1.sync_all(store2)
    elif len(args) == 1:
        store1 = get_record_store(args[0])
        project = load_project()
        store2 = project.record_store
        collisions = store1.sync(store2, project.name)
    else:
        parser.error('%prog sync requires either one or two arguments.')
    
    if collisions:
        print "Synchronization incomplete: there are two records with the same name for the following: %s" % ", ".join(collisions)
        sys.exit(1)
