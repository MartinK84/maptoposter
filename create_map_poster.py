import osmnx as ox
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import matplotlib.colors as mcolors
import numpy as np
from geopy.geocoders import Nominatim
from tqdm import tqdm
import time
import json
import os
from datetime import datetime
import argparse
import pickle
import tkinter as tk
from tkinter import ttk, messagebox, colorchooser, simpledialog
from PIL import Image, ImageTk
import threading
import glob

THEMES_DIR = "themes"
FONTS_DIR = "fonts"
POSTERS_DIR = "posters"
CACHE_DIR = "cache"

def load_fonts():
    """
    Load Roboto fonts from the fonts directory.
    Returns dict with font paths for different weights.
    """
    fonts = {
        'bold': os.path.join(FONTS_DIR, 'Roboto-Bold.ttf'),
        'regular': os.path.join(FONTS_DIR, 'Roboto-Regular.ttf'),
        'light': os.path.join(FONTS_DIR, 'Roboto-Light.ttf')
    }
    
    # Verify fonts exist
    for weight, path in fonts.items():
        if not os.path.exists(path):
            print(f"⚠ Font not found: {path}")
            return None
    
    return fonts

FONTS = load_fonts()

def generate_output_filename(city, theme_name):
    """
    Generate unique output filename with city, theme, and datetime.
    """
    if not os.path.exists(POSTERS_DIR):
        os.makedirs(POSTERS_DIR)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    city_slug = city.lower().replace(' ', '_')
    filename = f"{city_slug}_{theme_name}_{timestamp}.png"
    return os.path.join(POSTERS_DIR, filename)

def get_available_themes():
    """
    Scans the themes directory and returns a list of available theme names.
    """
    if not os.path.exists(THEMES_DIR):
        os.makedirs(THEMES_DIR)
        return []
    
    themes = []
    for file in sorted(os.listdir(THEMES_DIR)):
        if file.endswith('.json'):
            theme_name = file[:-5]  # Remove .json extension
            themes.append(theme_name)
    return themes

def load_theme(theme_name="feature_based"):
    """
    Load theme from JSON file in themes directory.
    """
    theme_file = os.path.join(THEMES_DIR, f"{theme_name}.json")
    
    if not os.path.exists(theme_file):
        print(f"⚠ Theme file '{theme_file}' not found. Using default feature_based theme.")
        # Fallback to embedded default theme
        return {
            "name": "Feature-Based Shading",
            "bg": "#FFFFFF",
            "text": "#000000",
            "gradient_color": "#FFFFFF",
            "water": "#C0C0C0",
            "parks": "#F0F0F0",
            "road_motorway": "#0A0A0A",
            "road_primary": "#1A1A1A",
            "road_secondary": "#2A2A2A",
            "road_tertiary": "#3A3A3A",
            "road_residential": "#4A4A4A",
            "road_default": "#3A3A3A"
        }
    
    with open(theme_file, 'r') as f:
        theme = json.load(f)
        print(f"✓ Loaded theme: {theme.get('name', theme_name)}")
        if 'description' in theme:
            print(f"  {theme['description']}")
        return theme

# Load theme (can be changed via command line or input)
THEME = None  # Will be loaded later

def create_gradient_fade(ax, color, location='bottom', zorder=10):
    """
    Creates a fade effect at the top or bottom of the map.
    """
    vals = np.linspace(0, 1, 256).reshape(-1, 1)
    gradient = np.hstack((vals, vals))
    
    rgb = mcolors.to_rgb(color)
    my_colors = np.zeros((256, 4))
    my_colors[:, 0] = rgb[0]
    my_colors[:, 1] = rgb[1]
    my_colors[:, 2] = rgb[2]
    
    if location == 'bottom':
        my_colors[:, 3] = np.linspace(1, 0, 256)
        extent_y_start = 0
        extent_y_end = 0.25
    else:
        my_colors[:, 3] = np.linspace(0, 1, 256)
        extent_y_start = 0.75
        extent_y_end = 1.0

    custom_cmap = mcolors.ListedColormap(my_colors)
    
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    y_range = ylim[1] - ylim[0]
    
    y_bottom = ylim[0] + y_range * extent_y_start
    y_top = ylim[0] + y_range * extent_y_end
    
    ax.imshow(gradient, extent=[xlim[0], xlim[1], y_bottom, y_top], 
              aspect='auto', cmap=custom_cmap, zorder=zorder, origin='lower')

def get_edge_colors_by_type(G):
    """
    Assigns colors to edges based on road type hierarchy.
    Returns a list of colors corresponding to each edge in the graph.
    """
    edge_colors = []
    
    for u, v, data in G.edges(data=True):
        # Get the highway type (can be a list or string)
        highway = data.get('highway', 'unclassified')
        
        # Handle list of highway types (take the first one)
        if isinstance(highway, list):
            highway = highway[0] if highway else 'unclassified'
        
        # Assign color based on road type
        if highway in ['motorway', 'motorway_link']:
            color = THEME['road_motorway']
        elif highway in ['trunk', 'trunk_link', 'primary', 'primary_link']:
            color = THEME['road_primary']
        elif highway in ['secondary', 'secondary_link']:
            color = THEME['road_secondary']
        elif highway in ['tertiary', 'tertiary_link']:
            color = THEME['road_tertiary']
        elif highway in ['residential', 'living_street', 'unclassified']:
            color = THEME['road_residential']
        else:
            color = THEME['road_default']
        
        edge_colors.append(color)
    
    return edge_colors

def get_edge_widths_by_type(G):
    """
    Assigns line widths to edges based on road type.
    Major roads get thicker lines.
    """
    edge_widths = []
    
    for u, v, data in G.edges(data=True):
        highway = data.get('highway', 'unclassified')
        
        if isinstance(highway, list):
            highway = highway[0] if highway else 'unclassified'
        
        # Assign width based on road importance
        if highway in ['motorway', 'motorway_link']:
            width = 1.2
        elif highway in ['trunk', 'trunk_link', 'primary', 'primary_link']:
            width = 1.0
        elif highway in ['secondary', 'secondary_link']:
            width = 0.8
        elif highway in ['tertiary', 'tertiary_link']:
            width = 0.6
        else:
            width = 0.4
        
        edge_widths.append(width)
    
    return edge_widths

def get_coordinates(city, country):
    """
    Fetches coordinates for a given city and country using geopy.
    Includes rate limiting to be respectful to the geocoding service.
    """
    print("Looking up coordinates...")
    geolocator = Nominatim(user_agent="city_map_poster")
    
    # Add a small delay to respect Nominatim's usage policy
    time.sleep(1)
    
    location = geolocator.geocode(f"{city}, {country}")
    
    if location:
        print(f"✓ Found: {location.address}")
        print(f"✓ Coordinates: {location.latitude}, {location.longitude}")
        return (location.latitude, location.longitude)
    else:
        raise ValueError(f"Could not find coordinates for {city}, {country}")

def get_map_data(city, country, point, dist):
    """
    Fetch map data from cache or download if not available.
    Returns (G, water, parks).
    """
    # Ensure cache directory exists
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    
    # Generate cache filename
    city_slug = city.lower().replace(' ', '_')
    country_slug = country.lower().replace(' ', '_')
    cache_filename = f"{city_slug}_{country_slug}_{dist}.pkl"
    cache_path = os.path.join(CACHE_DIR, cache_filename)
    
    # Check cache
    if os.path.exists(cache_path):
        print(f"✓ Found cached data: {cache_path}")
        try:
            with open(cache_path, 'rb') as f:
                print("  Loading from cache...")
                data = pickle.load(f)
                print("✓ Data loaded from cache")
                return data
        except Exception as e:
            print(f"⚠ Error loading cache: {e}. Downloading fresh data.")
    
    # Download if not cached
    print("  Cache miss. Downloading fresh data...")
    
    # Progress bar for data fetching
    with tqdm(total=3, desc="Fetching map data", unit="step", bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}') as pbar:
        # 1. Fetch Street Network
        pbar.set_description("Downloading street network")
        G = ox.graph_from_point(point, dist=dist, dist_type='bbox', network_type='all')
        pbar.update(1)
        time.sleep(0.5)  # Rate limit between requests
        
        # 2. Fetch Water Features
        pbar.set_description("Downloading water features")
        try:
            water = ox.features_from_point(point, tags={'natural': 'water', 'waterway': 'riverbank'}, dist=dist)
        except:
            water = None
        pbar.update(1)
        time.sleep(0.3)
        
        # 3. Fetch Parks
        pbar.set_description("Downloading parks/green spaces")
        try:
            parks = ox.features_from_point(point, tags={'leisure': 'park', 'landuse': 'grass'}, dist=dist)
        except:
            parks = None
        pbar.update(1)
        
    # Save to cache
    try:
        with open(cache_path, 'wb') as f:
            pickle.dump((G, water, parks), f)
        print(f"✓ Data saved to cache: {cache_path}")
    except Exception as e:
        print(f"⚠ Could not save to cache: {e}")
        
    return G, water, parks

def create_poster(city, country, point, dist, output_file):
    print(f"\nGenerating map for {city}, {country}...")
    
    # Get data (cached or fresh)
    G, water, parks = get_map_data(city, country, point, dist)
    
    # 2. Setup Plot
    print("Rendering map...")
    fig, ax = plt.subplots(figsize=(12, 16), facecolor=THEME['bg'])
    ax.set_facecolor(THEME['bg'])
    ax.set_position([0, 0, 1, 1])
    
    # 3. Plot Layers
    # Layer 1: Polygons
    if water is not None and not water.empty:
        water.plot(ax=ax, facecolor=THEME['water'], edgecolor='none', zorder=1)
    if parks is not None and not parks.empty:
        parks.plot(ax=ax, facecolor=THEME['parks'], edgecolor='none', zorder=2)
    
    # Layer 2: Roads with hierarchy coloring
    print("Applying road hierarchy colors...")
    edge_colors = get_edge_colors_by_type(G)
    edge_widths = get_edge_widths_by_type(G)
    
    ox.plot_graph(
        G, ax=ax, bgcolor=THEME['bg'],
        node_size=0,
        edge_color=edge_colors,
        edge_linewidth=edge_widths,
        show=False, close=False
    )
    
    # Layer 3: Gradients (Top and Bottom)
    create_gradient_fade(ax, THEME['gradient_color'], location='bottom', zorder=10)
    create_gradient_fade(ax, THEME['gradient_color'], location='top', zorder=10)
    
    # 4. Typography using Roboto font
    if FONTS:
        font_main = FontProperties(fname=FONTS['bold'], size=60)
        font_top = FontProperties(fname=FONTS['bold'], size=40)
        font_sub = FontProperties(fname=FONTS['light'], size=22)
        font_coords = FontProperties(fname=FONTS['regular'], size=14)
    else:
        # Fallback to system fonts
        font_main = FontProperties(family='monospace', weight='bold', size=60)
        font_top = FontProperties(family='monospace', weight='bold', size=40)
        font_sub = FontProperties(family='monospace', weight='normal', size=22)
        font_coords = FontProperties(family='monospace', size=14)
    
    spaced_city = "  ".join(list(city.upper()))

    # --- BOTTOM TEXT ---
    ax.text(0.5, 0.14, spaced_city, transform=ax.transAxes,
            color=THEME['text'], ha='center', fontproperties=font_main, zorder=11)
    
    ax.text(0.5, 0.10, country.upper(), transform=ax.transAxes,
            color=THEME['text'], ha='center', fontproperties=font_sub, zorder=11)
    
    lat, lon = point
    coords = f"{lat:.4f}° N / {lon:.4f}° E" if lat >= 0 else f"{abs(lat):.4f}° S / {lon:.4f}° E"
    if lon < 0:
        coords = coords.replace("E", "W")
    
    ax.text(0.5, 0.07, coords, transform=ax.transAxes,
            color=THEME['text'], alpha=0.7, ha='center', fontproperties=font_coords, zorder=11)
    
    ax.plot([0.4, 0.6], [0.125, 0.125], transform=ax.transAxes, 
            color=THEME['text'], linewidth=1, zorder=11)

    # --- ATTRIBUTION (bottom right) ---
    if FONTS:
        font_attr = FontProperties(fname=FONTS['light'], size=8)
    else:
        font_attr = FontProperties(family='monospace', size=8)
    
    ax.text(0.98, 0.02, "© OpenStreetMap contributors", transform=ax.transAxes,
            color=THEME['text'], alpha=0.5, ha='right', va='bottom', 
            fontproperties=font_attr, zorder=11)

    # 5. Save
    print(f"Saving to {output_file}...")
    plt.savefig(output_file, dpi=300, facecolor=THEME['bg'])
    plt.close()
    print(f"✓ Done! Poster saved as {output_file}")

def print_examples():
    """Print usage examples."""
    print("""
City Map Poster Generator
=========================

Usage:
  python create_map_poster.py --city <city> --country <country> [options]

Examples:
  # Iconic grid patterns
  python create_map_poster.py -c "New York" -C "USA" -t noir -d 12000           # Manhattan grid
  python create_map_poster.py -c "Barcelona" -C "Spain" -t warm_beige -d 8000   # Eixample district grid
  
  # Waterfront & canals
  python create_map_poster.py -c "Venice" -C "Italy" -t blueprint -d 4000       # Canal network
  python create_map_poster.py -c "Amsterdam" -C "Netherlands" -t ocean -d 6000  # Concentric canals
  python create_map_poster.py -c "Dubai" -C "UAE" -t midnight_blue -d 15000     # Palm & coastline
  
  # Radial patterns
  python create_map_poster.py -c "Paris" -C "France" -t pastel_dream -d 10000   # Haussmann boulevards
  python create_map_poster.py -c "Moscow" -C "Russia" -t noir -d 12000          # Ring roads
  
  # Organic old cities
  python create_map_poster.py -c "Tokyo" -C "Japan" -t japanese_ink -d 15000    # Dense organic streets
  python create_map_poster.py -c "Marrakech" -C "Morocco" -t terracotta -d 5000 # Medina maze
  python create_map_poster.py -c "Rome" -C "Italy" -t warm_beige -d 8000        # Ancient street layout
  
  # Coastal cities
  python create_map_poster.py -c "San Francisco" -C "USA" -t sunset -d 10000    # Peninsula grid
  python create_map_poster.py -c "Sydney" -C "Australia" -t ocean -d 12000      # Harbor city
  python create_map_poster.py -c "Mumbai" -C "India" -t contrast_zones -d 18000 # Coastal peninsula
  
  # River cities
  python create_map_poster.py -c "London" -C "UK" -t noir -d 15000              # Thames curves
  python create_map_poster.py -c "Budapest" -C "Hungary" -t copper_patina -d 8000  # Danube split
  
  # List themes
  python create_map_poster.py --list-themes

Options:
  --city, -c        City name (required)
  --country, -C     Country name (required)
  --theme, -t       Theme name (default: feature_based)
  --distance, -d    Map radius in meters (default: 29000)
  --list-themes     List all available themes

Distance guide:
  4000-6000m   Small/dense cities (Venice, Amsterdam old center)
  8000-12000m  Medium cities, focused downtown (Paris, Barcelona)
  15000-20000m Large metros, full city view (Tokyo, Mumbai)

Available themes can be found in the 'themes/' directory.
Generated posters are saved to 'posters/' directory.
""")

def list_themes():
    """List all available themes with descriptions."""
    available_themes = get_available_themes()
    if not available_themes:
        print("No themes found in 'themes/' directory.")
        return
    
    print("\nAvailable Themes:")
    print("-" * 60)
    for theme_name in available_themes:
        theme_path = os.path.join(THEMES_DIR, f"{theme_name}.json")
        try:
            with open(theme_path, 'r') as f:
                theme_data = json.load(f)
                display_name = theme_data.get('name', theme_name)
                description = theme_data.get('description', '')
        except:
            display_name = theme_name
            description = ''
        print(f"  {theme_name}")
        print(f"    {display_name}")
        if description:
            print(f"    {description}")
        print()

class MapPosterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("City Map Poster Generator")
        self.root.geometry("1200x800")
        
        # Use Agg backend to avoid thread issues with matplotlib
        plt.switch_backend('Agg')
        
        self.current_theme_data = load_theme("feature_based")
        global THEME
        THEME = self.current_theme_data
        
        self.setup_ui()
        # Defer loading image to ensure widgets are sized
        self.root.after(100, self.load_last_poster)

    def setup_ui(self):
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Left Panel - Controls
        left_panel = ttk.Frame(main_frame, width=350)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        
        # Right Panel - Preview
        self.right_panel = ttk.Frame(main_frame)
        self.right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        # Controls
        input_group = ttk.LabelFrame(left_panel, text="Map Settings", padding=10)
        input_group.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(input_group, text="City:").pack(anchor=tk.W)
        self.city_var = tk.StringVar()
        ttk.Entry(input_group, textvariable=self.city_var).pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(input_group, text="Country:").pack(anchor=tk.W)
        self.country_var = tk.StringVar()
        ttk.Entry(input_group, textvariable=self.country_var).pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(input_group, text="Radius (m):").pack(anchor=tk.W)
        self.dist_var = tk.IntVar(value=29000)
        ttk.Entry(input_group, textvariable=self.dist_var).pack(fill=tk.X, pady=(0, 5))
        
        theme_group = ttk.LabelFrame(left_panel, text="Theme", padding=10)
        theme_group.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(theme_group, text="Template:").pack(anchor=tk.W)
        self.theme_var = tk.StringVar(value="feature_based")
        self.theme_combo = ttk.Combobox(theme_group, textvariable=self.theme_var)
        self.theme_combo['values'] = get_available_themes()
        self.theme_combo.pack(fill=tk.X, pady=(0, 10))
        self.theme_combo.bind('<<ComboboxSelected>>', self.on_theme_change)
        ttk.Button(theme_group, text="Save Theme", command=self.save_theme).pack(fill=tk.X, pady=(0, 5))
        
        self.colors_frame = ttk.LabelFrame(left_panel, text="Adjust Colors", padding=10)
        self.colors_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        self.color_container = ttk.Frame(self.colors_frame)
        self.color_container.pack(fill=tk.BOTH, expand=True)
        self.refresh_color_buttons()
        
        self.gen_btn = ttk.Button(left_panel, text="Generate Poster", command=self.generate_map)
        self.gen_btn.pack(fill=tk.X, ipady=10, pady=(0, 10))
        
        self.status_label = ttk.Label(left_panel, text="Ready", wraplength=300)
        self.status_label.pack(fill=tk.X)
        
        self.preview_label = ttk.Label(self.right_panel, text="No poster generated yet", anchor=tk.CENTER)
        self.preview_label.pack(fill=tk.BOTH, expand=True)

    def refresh_color_buttons(self):
        for widget in self.color_container.winfo_children():
            widget.destroy()
        
        color_keys = [k for k in self.current_theme_data.keys() 
                     if k.startswith(('bg', 'text', 'water', 'parks', 'road', 'gradient')) 
                     and isinstance(self.current_theme_data[k], str) 
                     and self.current_theme_data[k].startswith('#')]
        
        for i, key in enumerate(sorted(color_keys)):
            lbl = ttk.Label(self.color_container, text=key.replace('road_', ''))
            lbl.grid(row=i, column=0, sticky=tk.W, padx=2, pady=2)
            btn = tk.Button(self.color_container, bg=self.current_theme_data[key], width=4,
                           command=lambda k=key: self.pick_color(k))
            btn.grid(row=i, column=1, sticky=tk.E, padx=2, pady=2)

    def pick_color(self, key):
        curr = self.current_theme_data.get(key, '#FFFFFF')
        color = colorchooser.askcolor(color=curr, title=f"Choose {key}")
        if color[1]:
            self.current_theme_data[key] = color[1]
            self.refresh_color_buttons()
            global THEME
            THEME = self.current_theme_data

    def on_theme_change(self, event):
        self.current_theme_data = load_theme(self.theme_var.get())
        global THEME
        THEME = self.current_theme_data
        self.refresh_color_buttons()

    def save_theme(self):
        current_name = self.theme_var.get()
        new_name = simpledialog.askstring("Save Theme", "Enter theme name (will be converted to snake_case filename):", 
                                        initialvalue=current_name, parent=self.root)
        
        if not new_name:
            return
            
        # Create filename slug (snake_case)
        slug = new_name.lower().replace(' ', '_')
        slug = "".join([c for c in slug if c.isalnum() or c == '_'])
        
        if not slug:
            messagebox.showerror("Error", "Invalid theme name")
            return
            
        filename = f"{slug}.json"
        filepath = os.path.join(THEMES_DIR, filename)
        
        if os.path.exists(filepath):
            if not messagebox.askyesno("Confirm Overwrite", f"Theme '{slug}' already exists. Overwrite?"):
                return
        
        self.current_theme_data['name'] = new_name
        try:
            with open(filepath, 'w') as f:
                json.dump(self.current_theme_data, f, indent=2)
            messagebox.showinfo("Success", f"Theme saved as '{slug}'")
            self.theme_combo['values'] = get_available_themes()
            self.theme_var.set(slug)
        except Exception as e:
            messagebox.showerror("Error", f"Could not save theme: {e}")

    def load_last_poster(self):
        if not os.path.exists(POSTERS_DIR): return
        files = glob.glob(os.path.join(POSTERS_DIR, "*.png"))
        if files:
            self.show_image(max(files, key=os.path.getctime))

    def show_image(self, path):
        try:
            img = Image.open(path)
            w_avail = self.preview_label.winfo_width() or 800
            h_avail = self.preview_label.winfo_height() or 800
            
            ratio = min(w_avail/img.width, h_avail/img.height)
            new_size = (int(img.width*ratio), int(img.height*ratio))
            
            resample = getattr(Image, 'Resampling', Image).LANCZOS
            img = img.resize(new_size, resample)
            self.photo = ImageTk.PhotoImage(img)
            self.preview_label.configure(image=self.photo, text="")
        except Exception as e:
            print(f"Error showing image: {e}")

    def generate_map(self):
        city = self.city_var.get()
        country = self.country_var.get()
        try: dist = self.dist_var.get()
        except: return messagebox.showerror("Error", "Invalid distance")
        
        if not city or not country:
            return messagebox.showerror("Error", "City and Country required")
            
        self.gen_btn.state(['disabled'])
        self.status_label.config(text="Generating... Check console for progress.")
        threading.Thread(target=self.run_generation, args=(city, country, dist)).start()

    def run_generation(self, city, country, dist):
        try:
            global THEME
            THEME = self.current_theme_data
            coords = get_coordinates(city, country)
            output_file = generate_output_filename(city, self.theme_var.get())
            create_poster(city, country, coords, dist, output_file)
            self.root.after(0, lambda: self.on_success(output_file))
        except Exception as e:
            self.root.after(0, lambda: self.on_error(str(e)))

    def on_success(self, output_file):
        self.status_label.config(text=f"Saved: {os.path.basename(output_file)}")
        self.show_image(output_file)
        self.gen_btn.state(['!disabled'])

    def on_error(self, msg):
        self.status_label.config(text=f"Error: {msg}")
        messagebox.showerror("Error", msg)
        self.gen_btn.state(['!disabled'])

def start_gui():
    root = tk.Tk()
    app = MapPosterApp(root)
    root.mainloop()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate beautiful map posters for any city",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python create_map_poster.py --city "New York" --country "USA"
  python create_map_poster.py --city Tokyo --country Japan --theme midnight_blue
  python create_map_poster.py --city Paris --country France --theme noir --distance 15000
  python create_map_poster.py --list-themes
        """
    )
    
    parser.add_argument('--city', '-c', type=str, help='City name')
    parser.add_argument('--country', '-C', type=str, help='Country name')
    parser.add_argument('--theme', '-t', type=str, default='feature_based', help='Theme name (default: feature_based)')
    parser.add_argument('--distance', '-d', type=int, default=29000, help='Map radius in meters (default: 29000)')
    parser.add_argument('--list-themes', action='store_true', help='List all available themes')
    parser.add_argument('--gui', '-g', action='store_true', help='Launch Graphical User Interface')
    
    args = parser.parse_args()
    
    # Launch GUI if requested
    if args.gui:
        start_gui()
        os.sys.exit(0)

    # If no arguments provided, show examples
    if len(os.sys.argv) == 1:
        print_examples()
        os.sys.exit(0)
    
    # List themes if requested
    if args.list_themes:
        list_themes()
        os.sys.exit(0)
    
    # Validate required arguments
    if not args.city or not args.country:
        print("Error: --city and --country are required.\n")
        print_examples()
        os.sys.exit(1)
    
    # Validate theme exists
    available_themes = get_available_themes()
    if args.theme not in available_themes:
        print(f"Error: Theme '{args.theme}' not found.")
        print(f"Available themes: {', '.join(available_themes)}")
        os.sys.exit(1)
    
    print("=" * 50)
    print("City Map Poster Generator")
    print("=" * 50)
    
    # Load theme
    THEME = load_theme(args.theme)
    
    # Get coordinates and generate poster
    try:
        coords = get_coordinates(args.city, args.country)
        output_file = generate_output_filename(args.city, args.theme)
        create_poster(args.city, args.country, coords, args.distance, output_file)
        
        print("\n" + "=" * 50)
        print("✓ Poster generation complete!")
        print("=" * 50)
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        os.sys.exit(1)
