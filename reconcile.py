"""The main program."""

import os
from os.path import basename
import sys
import zipfile
import argparse
import textwrap
import lib.util as util
import lib.reconciler as reconciler
import lib.summary as summary
import lib.merged as merged

VERSION = '0.4.4'


def parse_command_line():
    """Get user input."""
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        fromfile_prefix_chars='@',
        description=textwrap.dedent("""
            This takes raw Notes from Nature classifications and creates a
            reconciliation of the classifications for a particular workflow.
            That is, it reduces n classifications per subject to the "best"
            values. The summary file will provide explanations of how the
            reconciliations were done. NOTE: You may use a file to hold the
            command-line arguments like: @/path/to/args.txt."""),
        epilog=textwrap.dedent("""
            Current reconciliation types
            ----------------------------
              select: Reconcile a fixed list of options.
              text:   Reconcile free text entries.
              same:   Check that all items in a group are the same.
              mmr:    Show the mean, mode, and range for each group.
            * Note:   If a column is not listed it will not be reconciled."""))

    parser.add_argument('input_file', metavar="INPUT-FILE",
                        help="""The input file.""")

    parser.add_argument('-f', '--format',
                        choices=['nfn', 'csv', 'json'], default='nfn',
                        help="""The unreconciled data is in what type of file?
                             nfn=A Zooniverse classification data dump.
                             csv=A flat CSV file. json=A JSON file. The
                             default is "nfn". When the format is "csv" or
                             "json" we require the --column-types. If the
                             type is "nfn" we can guess the --column-types
                             but the --column-types option will still override
                             our guesses.""")

    parser.add_argument('-c', '--column-types', action='append',
                        help="""A string with information on how to reconcile
                             each column in the input file. The format is
                             --column-types "foo foo:select,bar:text,baz:text".
                             The list is comma separated with the column
                             label going before the colon and the
                             reconciliation type after the colon. Note: This
                             overrides any column type guesses. You may use
                             this multiple times.""")

    parser.add_argument('--user-weights',  default='',
                        help="""A string with user IDs and corresponding
                             weights. Used to favor contributions from specific
                             users when using the "text" column type. The
                             format is --user-weights "foo:-10,bar:25". The
                             list is comma separated with the user ID going
                             before the colon and the weight after the colon.
                             Note: This weight is added to the fuzzywuzzy
                             score, which is a percentage.
                             --user-weights "aSmith:70" would very often
                             select aSmith's transcriptions.
                             --user-weights "aSmith:10" would add 10 to all of
                             aSmith's scores.
                             --user-weights "aSmith:-50" would distrust
                             aSmith's transcriptions.""")

    parser.add_argument('-u', '--unreconciled',
                        help="""Write the unreconciled workflow
                            classifications to this CSV file.""")

    parser.add_argument('-r', '--reconciled',
                        help="""Write the reconciled classifications to this
                            CSV file.""")

    parser.add_argument('-s', '--summary',
                        help="""Write a summary of the reconciliation to this
                            HTML file.""")

    parser.add_argument('-m', '--merged',
                        help="""Write the merged reconciled data, explanations,
                            and unreconciled data to this CSV file.""")

    parser.add_argument('-z', '--zip',
                        help="""Zip files and put them into this archive.
                            Remove the uncompressed files afterwards.""")

    parser.add_argument('-w', '--workflow-id', type=int,
                        help="""The workflow to extract. Required if there is
                             more than one workflow in the classifications
                             file. This is only used for nfn formats.""")

    parser.add_argument('--title', default='',
                        help="""The title to put on the summary report. We will
                            build this when the format is nfn. For other
                            formats the default is the INPUT-FILE.""")

    parser.add_argument('--group-by', default='subject_id',
                        help="""Group the rows by this column
                            (Default=subject_id).""")

    parser.add_argument('--key-column', default='classification_id',
                        help="""The column containing the primary key
                            (Default=classification_id).""")

    parser.add_argument('--user-column',
                        help="""Which column to use to get a count of user
                            transcripts. For --format=nfn the
                            default=user_name for other formats there is no
                            default. This will affect which sections appear
                            on the summary report.""")

    parser.add_argument('--page-size', default=20, type=int,
                        help="""Page size for the summary report's detail
                            section (Default=20).""")

    parser.add_argument('--fuzzy-ratio-threshold', default=90, type=int,
                        help="""Sets the cutoff for fuzzy ratio matching
                            (0-100, default=90).
                            See https://github.com/seatgeek/fuzzywuzzy.""")

    parser.add_argument('--fuzzy-set-threshold', default=50, type=int,
                        help="""Sets the cutoff for fuzzy set matching (0-100,
                            default=50).
                            See https://github.com/seatgeek/fuzzywuzzy.""")

    parser.add_argument('-V', '--version', action='version',
                        version='%(prog)s {}'.format(VERSION))

    args = parser.parse_args()

    if args.user_weights:
        args.user_weights = {key.lower(): int(value) for key, value in
                             [(i.split(':')) for i in
                              args.user_weights.split(',')]}
    # user_weights is made case insensitive
    else:
        args.user_weights = {}

    if args.fuzzy_ratio_threshold < 0 or args.fuzzy_ratio_threshold > 100:
        print('--fuzzy-ratio-threshold must be between 0 and 100.')
        sys.exit(1)

    if args.fuzzy_set_threshold < 0 or args.fuzzy_set_threshold > 100:
        print('--fuzzy-set-threshold must be between 0 and 100.')
        sys.exit(1)

    return args


def zip_files(args):
    """Put results into a zip file."""
    zip_file = args.zip if args.zip else args.zip_keep

    with zipfile.ZipFile(zip_file, mode='w') as zippy:
        if args.unreconciled:
            zippy.write(args.unreconciled,
                        arcname=basename(args.unreconciled),
                        compress_type=zipfile.ZIP_DEFLATED)
        if args.reconciled:
            zippy.write(args.reconciled,
                        arcname=basename(args.reconciled),
                        compress_type=zipfile.ZIP_DEFLATED)
        if args.summary:
            zippy.write(args.summary,
                        arcname=basename(args.summary),
                        compress_type=zipfile.ZIP_DEFLATED)
        if args.merged:
            zippy.write(args.merged,
                        arcname=basename(args.merged),
                        compress_type=zipfile.ZIP_DEFLATED)

    if args.unreconciled:
        os.remove(args.unreconciled)
    if args.reconciled:
        os.remove(args.reconciled)
    if args.summary:
        os.remove(args.summary)
    if args.merged:
        os.remove(args.merged)


def get_column_types(args, column_types):
    """Append the argument column types to the inferred column types."""
    last = util.last_column_type(column_types)
    if args.column_types:
        for arg in args.column_types:
            for option in arg.split(','):
                name, col_type = option.split(':')
                name = name.strip()
                col_type = col_type.strip()
                if column_types.get(name):
                    order = column_types[name]['order']
                else:
                    last += 1
                    order = last
                column_types[name] = {'type': col_type,
                                      'order': order,
                                      'name': name}
    return column_types


def validate_columns(args, column_types, unreconciled, plugins=None):
    """Validate that the columns are in the unreconciled data frame.

    Also verify that the column types are an existing plug-in.
    """
    has_errors = False
    types = list(plugins.keys())
    for column, column_type in column_types.items():
        if column not in unreconciled.columns:
            has_errors = True
            print('ERROR: "{}" is not a column header'.format(column))
        if column_type['type'] not in types:
            has_errors = True
            print('ERROR: "{}" is not a column type'.format(
                column_type['type']))

    for column in [args.group_by, args.key_column]:
        if column not in unreconciled.columns:
            has_errors = True
            print('ERROR: "{}" is not a column header'.format(column))

        if has_errors:
            print('\nValid column types are: {}\n'.format(types))
            print('Valid column headers are:')
            for col in unreconciled.columns:
                print('\t{}'.format(col))
            print('* Please remember that "--format=nfn" may rename column '
                  'headers.')
            sys.exit(1)


def main():
    """Reconcile the data."""
    args = parse_command_line()

    formats = util.get_plugins('formats')
    unreconciled, column_types = formats[args.format].read(args)

    if unreconciled.shape[0] == 0:
        sys.exit('Workflow {} has no data.'.format(args.workflow_id))

    plugins = util.get_plugins('column_types')
    column_types = get_column_types(args, column_types)
    validate_columns(args, column_types, unreconciled, plugins=plugins)

    if args.unreconciled:
        unreconciled.to_csv(args.unreconciled, index=False)

    if args.reconciled or args.summary or args.merged:
        reconciled, explanations = reconciler.build(
            args, unreconciled, column_types, plugins=plugins)

        if args.reconciled:
            columns = util.sort_columns(args, reconciled.columns, column_types)
            del columns[0]
            del columns[0]
            del columns[0]
            reconciled = reconciled.reindex(columns, axis=1).fillna('')
            reconciled.to_csv(args.reconciled)

        if args.summary:
            summary.report(
                args, unreconciled, reconciled, explanations, column_types)

        if args.merged:
            smerged = merged.merge(
                args, unreconciled, reconciled, explanations, column_types)
            smerged.to_csv(args.merged, index=False)

    if args.zip:
        zip_files(args)


if __name__ == "__main__":
    main()
