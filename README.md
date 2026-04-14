# MRI Exhibit

Python-based touch-screen exhibit for a museum MRI activity.

## Features
- YAML-driven exhibit content
- Touch/button-based navigation
- Barcode/keyboard animal selection
- Static image display for MRI results
- Activity timeout back to attract screen

## Project Structure
- `main.py` - main application
- `assets/` - images and display assets
- `data/` - YAML exhibit definitions
- `tests/` - regression tests
- `requirements.txt` - Python dependencies

## Setup
* Install the latest version of python from python.org
* Create a virtual environment with "python -m venv venv"
* Activate the environment with "venv\Scripts\activate"
* Get the latest version of pip with "python -m pip install --upgrade pip
* Install dependencies from `requirements.txt` with "pip install -r requirements.txt"
* Run the application with "python main.py"

## Setup on Windows 10
* Install python 3.12 from python.org
* Create a virtual environment with "python -m venv venv"
* Activate the environment with "venv\Scripts\activate"
* get the latest pip with "python -m pip install --upgrade pip'
* get the required packages with "pip install -r requirements.txt"
* run the program "python main.py"

## rPi Debian
* Install python 3.13.5 from python.org. Later versions don't work.
  Check with "python --version"
* Create a virtual environment with "python3 -m venv venv"
* activate the environment with ". venv/bin/activate"
  Note the "(venv)" at the beginning of the prompt
* get the required packages with "pip install -r requirements.txt"
* install the RPi GPIO with "pip install RPi.GPIO"
* run the program with "python main.py"

