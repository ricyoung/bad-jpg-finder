#!/usr/bin/env python3
"""
Bad Image Finder - A tool to find and clean up corrupt image files.
Author: Richard Young
License: MIT
"""

import os
import argparse
import concurrent.futures
import sys
import time
import io
import shutil
from pathlib import Path
from PIL import Image, ImageFile
from tqdm import tqdm
import tqdm.auto as tqdm_auto
import colorama
import humanize
import logging

# Initialize colorama (required for Windows)
colorama.init()

# Allow loading of truncated images for repair attempts
ImageFile.LOAD_TRUNCATED_IMAGES = True

# Dictionary of supported image formats with their extensions
SUPPORTED_FORMATS = {
    'JPEG': ('.jpg', '.jpeg', '.jpe', '.jif', '.jfif', '.jfi'),
    'PNG': ('.png',),
    'GIF': ('.gif',),
    'TIFF': ('.tiff', '.tif'),
    'BMP': ('.bmp', '.dib'),
    'WEBP': ('.webp',),
    'ICO': ('.ico',),
    'HEIC': ('.heic',),
}

# Default formats (all supported formats)
DEFAULT_FORMATS = list(SUPPORTED_FORMATS.keys())

# List of formats that can potentially be repaired
REPAIRABLE_FORMATS = ['JPEG', 'PNG', 'GIF']

def setup_logging(verbose, no_color=False):
    level = logging.DEBUG if verbose else logging.INFO
    
    # Define color codes
    if not no_color:
        # Color scheme
        COLORS = {
            'DEBUG': colorama.Fore.CYAN,
            'INFO': colorama.Fore.GREEN,
            'WARNING': colorama.Fore.YELLOW,
            'ERROR': colorama.Fore.RED,
            'CRITICAL': colorama.Fore.MAGENTA + colorama.Style.BRIGHT,
            'RESET': colorama.Style.RESET_ALL
        }
        
        # Custom formatter with colors
        class ColoredFormatter(logging.Formatter):
            def format(self, record):
                levelname = record.levelname
                if levelname in COLORS:
                    record.levelname = f"{COLORS[levelname]}{levelname}{COLORS['RESET']}"
                    record.msg = f"{COLORS[levelname]}{record.msg}{COLORS['RESET']}"
                return super().format(record)
                
        formatter = ColoredFormatter('%(asctime)s - %(levelname)s - %(message)s')
    else:
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    
    logging.basicConfig(
        level=level,
        handlers=[handler]
    )

def diagnose_image_issue(file_path):
    """
    Attempts to diagnose what's wrong with the image.
    Returns: (error_type, details)
    """
    try:
        with open(file_path, 'rb') as f:
            header = f.read(16)  # Read first 16 bytes
        
        # Check for zero-byte file
        if len(header) == 0:
            return "empty_file", "File is empty (0 bytes)"
        
        # Check for correct JPEG header
        if file_path.lower().endswith(SUPPORTED_FORMATS['JPEG']):
            if not (header.startswith(b'\xff\xd8\xff')):
                return "invalid_header", "Invalid JPEG header"
        
        # Check for correct PNG header
        elif file_path.lower().endswith(SUPPORTED_FORMATS['PNG']):
            if not header.startswith(b'\x89PNG\r\n\x1a\n'):
                return "invalid_header", "Invalid PNG header"
        
        # Try to open with PIL for more detailed diagnosis
        try:
            with Image.open(file_path) as img:
                img.verify()
        except Exception as e:
            error_str = str(e).lower()
            
            if "truncated" in error_str:
                return "truncated", "File is truncated"
            elif "corrupt" in error_str:
                return "corrupt_data", "Data corruption detected"
            elif "incorrect mode" in error_str or "decoder" in error_str:
                return "decoder_issue", "Image decoder issue"
            else:
                return "unknown", f"Unknown issue: {str(e)}"
                
        # Now try to load the data
        try:
            with Image.open(file_path) as img:
                img.load()
        except Exception as e:
            return "data_load_failed", f"Image data couldn't be loaded: {str(e)}"
            
        # If we got here, there's some other issue
        return "unknown", "Unknown issue"
        
    except Exception as e:
        return "access_error", f"Error accessing file: {str(e)}"

def is_valid_image(file_path):
    """
    Validate image file integrity using PIL.
    Returns True if valid, False if corrupt.
    """
    try:
        with Image.open(file_path) as img:
            # verify() checks the file header
            img.verify()
            
            # Additional step: try to load the image data
            # This catches more corruption issues
            with Image.open(file_path) as img2:
                img2.load()
        return True
    except Exception as e:
        logging.debug(f"Invalid image {file_path}: {str(e)}")
        return False

def attempt_repair(file_path, backup_dir=None):
    """
    Attempts to repair corrupt image files.
    Returns: (success, message, fixed_width, fixed_height)
    """
    # Create backup if requested
    if backup_dir:
        backup_path = os.path.join(backup_dir, os.path.basename(file_path) + ".bak")
        try:
            shutil.copy2(file_path, backup_path)
            logging.debug(f"Created backup at {backup_path}")
        except Exception as e:
            logging.warning(f"Could not create backup: {str(e)}")
    
    try:
        # First, diagnose the issue
        issue_type, details = diagnose_image_issue(file_path)
        logging.debug(f"Diagnosis for {file_path}: {issue_type} - {details}")
        
        file_ext = os.path.splitext(file_path)[1].lower()
        
        # Check if file format is supported for repair
        format_supported = False
        for fmt in REPAIRABLE_FORMATS:
            if file_ext in SUPPORTED_FORMATS[fmt]:
                format_supported = True
                break
                
        if not format_supported:
            return False, f"Format not supported for repair ({file_ext})", None, None
        
        # Try to open and resave the image with PIL's error forgiveness
        # This works for many truncated files
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                format = img.format
                
                # Create a buffer for the fixed image
                buffer = io.BytesIO()
                img.save(buffer, format=format)
                
                # Write the repaired image back to the original file
                with open(file_path, 'wb') as f:
                    f.write(buffer.getvalue())
                
                # Verify the repaired image
                if is_valid_image(file_path):
                    return True, f"Repaired {issue_type} issue", width, height
                else:
                    # If verification fails, try again with JPEG specific options for JPEG files
                    if format == 'JPEG':
                        with Image.open(file_path) as img:
                            buffer = io.BytesIO()
                            # Use optimize=True and quality=85 for better repair chances
                            img.save(buffer, format='JPEG', optimize=True, quality=85)
                            with open(file_path, 'wb') as f:
                                f.write(buffer.getvalue())
                            
                            if is_valid_image(file_path):
                                return True, f"Repaired {issue_type} issue with JPEG optimization", width, height
                    
                    return False, f"Failed to repair {issue_type} issue", None, None
                    
        except Exception as e:
            logging.debug(f"Repair attempt failed for {file_path}: {str(e)}")
            return False, f"Repair failed: {str(e)}", None, None
            
    except Exception as e:
        logging.debug(f"Error during repair of {file_path}: {str(e)}")
        return False, f"Repair error: {str(e)}", None, None

def process_file(args):
    """Process a single image file."""
    file_path, repair_mode, repair_dir = args
    
    # Check if the image is valid
    is_valid = is_valid_image(file_path)
    
    if not is_valid and repair_mode:
        # Try to repair the file
        repair_success, repair_msg, width, height = attempt_repair(file_path, repair_dir)
        
        if repair_success:
            # File was repaired
            return file_path, True, 0, "repaired", repair_msg, (width, height)
        else:
            # File is still corrupt
            size = os.path.getsize(file_path)
            return file_path, False, size, "repair_failed", repair_msg, None
    else:
        # No repair attempted or file is valid
        size = os.path.getsize(file_path) if not is_valid else 0
        return file_path, is_valid, size, "not_repaired", None, None

def get_extensions_for_formats(formats):
    """Get all file extensions for the specified formats."""
    extensions = []
    for fmt in formats:
        if fmt in SUPPORTED_FORMATS:
            extensions.extend(SUPPORTED_FORMATS[fmt])
    return tuple(extensions)

def find_image_files(directory, formats, recursive=True):
    """Find all image files of specified formats in a directory."""
    image_files = []
    extensions = get_extensions_for_formats(formats)
    
    if not extensions:
        logging.warning("No valid image formats specified!")
        return []
    
    format_names = ", ".join(formats)
    if recursive:
        logging.info(f"Recursively scanning for {format_names} files...")
        for root, _, files in os.walk(directory):
            for file in files:
                if file.lower().endswith(extensions):
                    image_files.append(os.path.join(root, file))
    else:
        logging.info(f"Scanning for {format_names} files in {directory} (non-recursive)...")
        for file in os.listdir(directory):
            if os.path.isfile(os.path.join(directory, file)) and file.lower().endswith(extensions):
                image_files.append(os.path.join(directory, file))
    
    logging.info(f"Found {len(image_files)} image files")
    return image_files

def process_images(directory, formats, dry_run=True, repair=False, 
                  max_workers=None, recursive=True, move_to=None, repair_dir=None):
    """Find corrupt image files and optionally repair, delete, or move them."""
    start_time = time.time()
    bad_files = []
    repaired_files = []
    total_size_saved = 0
    
    # Find all image files
    image_files = find_image_files(directory, formats, recursive)
    if not image_files:
        logging.warning("No image files found!")
        return [], [], 0
        
    # Create directories if they don't exist
    if move_to and not os.path.exists(move_to):
        os.makedirs(move_to)
        logging.info(f"Created directory for corrupt files: {move_to}")
    
    if repair and repair_dir and not os.path.exists(repair_dir):
        os.makedirs(repair_dir)
        logging.info(f"Created directory for backup files: {repair_dir}")
    
    # Prepare input arguments for workers
    input_args = [(file_path, repair, repair_dir) for file_path in image_files]
    
    # Process files in parallel
    logging.info("Processing files in parallel...")
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Colorful progress bar
        results = list(tqdm_auto.tqdm(
            executor.map(process_file, input_args),
            total=len(image_files),
            desc=f"{colorama.Fore.BLUE}Checking image files{colorama.Style.RESET_ALL}",
            unit="file",
            bar_format="{desc}: {percentage:3.0f}%|{bar:30}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
            colour="blue"
        ))
    
    # Process results
    for file_path, is_valid, size, repair_status, repair_msg, dimensions in results:
        if repair_status == "repaired":
            # File was successfully repaired
            repaired_files.append(file_path)
            width, height = dimensions
            msg = f"Repaired: {file_path} ({width}x{height}) - {repair_msg}"
            logging.info(msg)
        elif not is_valid:
            # File is corrupt and wasn't repaired (or repair failed)
            bad_files.append(file_path)
            total_size_saved += size
            
            size_str = humanize.naturalsize(size)
            if repair_status == "repair_failed":
                fail_msg = f"Repair failed: {file_path} ({size_str}) - {repair_msg}"
                logging.warning(fail_msg)
                
            if dry_run:
                msg = f"Would delete: {file_path} ({size_str})"
                logging.info(msg)
            elif move_to:
                dest_path = os.path.join(move_to, os.path.basename(file_path))
                try:
                    os.rename(file_path, dest_path)
                    # Add arrow with color
                    arrow = f"{colorama.Fore.CYAN}→{colorama.Style.RESET_ALL}"
                    msg = f"Moved: {file_path} {arrow} {dest_path} ({size_str})"
                    logging.info(msg)
                except Exception as e:
                    logging.error(f"Failed to move {file_path}: {e}")
            else:
                try:
                    os.remove(file_path)
                    msg = f"Deleted: {file_path} ({size_str})"
                    logging.info(msg)
                except Exception as e:
                    logging.error(f"Failed to delete {file_path}: {e}")
    
    elapsed = time.time() - start_time
    logging.info(f"Processed {len(image_files)} files in {elapsed:.2f} seconds")
    
    return bad_files, repaired_files, total_size_saved

def main():
    parser = argparse.ArgumentParser(
        description='Find, repair, and manage corrupt image files',
        epilog='Created by Richard Young - https://github.com/ricyoung/bad-jpg-finder'
    )
    parser.add_argument('directory', help='Directory to search for image files')
    parser.add_argument('--delete', action='store_true', help='Delete corrupt image files (without this flag, runs in dry-run mode)')
    parser.add_argument('--move-to', type=str, help='Move corrupt files to this directory instead of deleting them')
    parser.add_argument('--workers', type=int, default=None, help='Number of worker processes (default: CPU count)')
    parser.add_argument('--non-recursive', action='store_true', help='Only search in the specified directory, not subdirectories')
    parser.add_argument('--output', type=str, help='Save list of corrupt files to this file')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    parser.add_argument('--no-color', action='store_true', help='Disable colored output')
    parser.add_argument('--version', action='version', version='Bad Image Finder v1.2.0 by Richard Young')
    
    # Repair options
    repair_group = parser.add_argument_group('Repair options')
    repair_group.add_argument('--repair', action='store_true', help='Attempt to repair corrupt image files')
    repair_group.add_argument('--backup-dir', type=str, help='Directory to store backups of files before repair')
    repair_group.add_argument('--repair-report', type=str, help='Save list of repaired files to this file')
    
    # Format options
    format_group = parser.add_argument_group('Image format options')
    format_group.add_argument('--formats', type=str, nargs='+', choices=SUPPORTED_FORMATS.keys(), 
                             help=f'Image formats to check (default: all formats)')
    format_group.add_argument('--jpeg', action='store_true', help='Check JPEG files only')
    format_group.add_argument('--png', action='store_true', help='Check PNG files only')
    format_group.add_argument('--tiff', action='store_true', help='Check TIFF files only')
    format_group.add_argument('--gif', action='store_true', help='Check GIF files only')
    format_group.add_argument('--bmp', action='store_true', help='Check BMP files only')
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.verbose, args.no_color)
    
    directory = Path(args.directory)
    if not directory.exists() or not directory.is_dir():
        logging.error(f"Error: {args.directory} is not a valid directory")
        sys.exit(1)
    
    # Check for incompatible options
    if args.delete and args.move_to:
        logging.error("Error: Cannot use both --delete and --move-to options")
        sys.exit(1)
    
    # Determine which formats to check
    formats = []
    if args.formats:
        formats = args.formats
    elif args.jpeg:
        formats.append('JPEG')
    elif args.png:
        formats.append('PNG')
    elif args.tiff:
        formats.append('TIFF')
    elif args.gif:
        formats.append('GIF')
    elif args.bmp:
        formats.append('BMP')
    else:
        # Default: check all formats
        formats = DEFAULT_FORMATS
    
    dry_run = not (args.delete or args.move_to)
    
    # Colorful mode indicators
    if args.repair:
        mode_str = f"{colorama.Fore.MAGENTA}REPAIR MODE{colorama.Style.RESET_ALL}: Attempting to fix corrupt files"
        logging.info(mode_str)
        
        repairable_formats = [fmt for fmt in formats if fmt in REPAIRABLE_FORMATS]
        if repairable_formats:
            logging.info(f"Repairable formats: {', '.join(repairable_formats)}")
        else:
            logging.warning("None of the selected formats support repair")
    
    if dry_run:
        mode_str = f"{colorama.Fore.YELLOW}DRY RUN MODE{colorama.Style.RESET_ALL}: No files will be deleted or moved"
        logging.info(mode_str)
    elif args.move_to:
        mode_str = f"{colorama.Fore.BLUE}MOVE MODE{colorama.Style.RESET_ALL}: Corrupt files will be moved to {args.move_to}"
        logging.info(mode_str)
    else:
        mode_str = f"{colorama.Fore.RED}DELETE MODE{colorama.Style.RESET_ALL}: Corrupt files will be permanently deleted"
        logging.info(mode_str)
    
    # Show which formats we're checking
    format_list = ", ".join(formats)
    logging.info(f"Checking image formats: {format_list}")
    logging.info(f"Searching for corrupt image files in {directory}")
    
    try:
        bad_files, repaired_files, total_size_saved = process_images(
            directory, 
            formats,
            dry_run=dry_run, 
            repair=args.repair,
            max_workers=args.workers,
            recursive=not args.non_recursive,
            move_to=args.move_to,
            repair_dir=args.backup_dir
        )
        
        # Colorful summary
        count_color = colorama.Fore.RED if bad_files else colorama.Fore.GREEN
        file_count = f"{count_color}{len(bad_files)}{colorama.Style.RESET_ALL}"
        logging.info(f"Found {file_count} corrupt image files")
        
        if args.repair:
            repair_color = colorama.Fore.GREEN if repaired_files else colorama.Fore.YELLOW
            repair_count = f"{repair_color}{len(repaired_files)}{colorama.Style.RESET_ALL}"
            logging.info(f"Successfully repaired {repair_count} files")
            
            if args.repair_report and repaired_files:
                with open(args.repair_report, 'w') as f:
                    for file_path in repaired_files:
                        f.write(f"{file_path}\n")
                logging.info(f"Saved list of repaired files to {args.repair_report}")
        
        savings_str = humanize.naturalsize(total_size_saved)
        savings_color = colorama.Fore.GREEN if total_size_saved > 0 else colorama.Fore.RESET
        savings_msg = f"Total space savings: {savings_color}{savings_str}{colorama.Style.RESET_ALL}"
        logging.info(savings_msg)
        
        if not args.no_color:
            # Add signature at the end of the run
            signature = f"\n{colorama.Fore.CYAN}Bad Image Finder v1.2.0 by Richard Young{colorama.Style.RESET_ALL}"
            print(signature)
        
        # Save list of corrupt files if requested
        if args.output and bad_files:
            with open(args.output, 'w') as f:
                for file_path in bad_files:
                    f.write(f"{file_path}\n")
            logging.info(f"Saved list of corrupt files to {args.output}")
        
        if bad_files and dry_run:
            logging.info("Run with --delete to remove these files or --move-to to relocate them")
            
    except KeyboardInterrupt:
        logging.info("Operation cancelled by user")
        sys.exit(130)
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()