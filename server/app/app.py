#################################################################
#################################################################
############### Notebook Generator Server #######################
#################################################################
#################################################################
##### Author: Denis Torre
##### Affiliation: Ma'ayan Laboratory,
##### Icahn School of Medicine at Mount Sinai

#######################################################
#######################################################
########## 1. App Configuration
#######################################################
#######################################################

#############################################
########## 1. Load libraries
#############################################
##### 1. Flask modules #####
from flask import Flask, request, render_template, Response, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_mail import Mail

##### 2. Python modules #####
# General
import sys, os, json, time, re, urllib.request
import pandas as pd
import h5py

# Database
from sqlalchemy.orm import sessionmaker
from sqlalchemy import MetaData
import pymysql
pymysql.install_as_MySQLdb()

# Sentry
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

##### 3. Custom modules #####
sys.path.append('app/static/py')
import NotebookGenerator as NG
import NotebookManager as NM

#############################################
########## 2. App Setup
#############################################
##### 1. Flask App #####
# Sentry
if os.getenv('SENTRY_DSN'):
	sentry_sdk.init(dsn=os.environ['SENTRY_DSN'], integrations=[FlaskIntegration()])

# General
with open('dev.txt') as openfile:
	dev = openfile.read() == 'True'
entry_point = '/notebook-generator-server-dev' if dev else '/notebook-generator-server'
app = Flask(__name__, static_url_path=os.path.join(entry_point, 'app/static'))

# Database
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['SQLALCHEMY_DATABASE_URI']#+'-dev'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
engine = db.engine
Session = sessionmaker(bind=engine)
metadata = MetaData()
metadata.reflect(bind=engine)
tables = metadata.tables

# Cross origin
CORS(app, resources=r'{}/api/*'.format(entry_point))

# Mail
mail_settings = {
    "MAIL_SERVER": 'smtp.gmail.com',
    "MAIL_PORT": 465,
    "MAIL_USE_SSL": True,
    "MAIL_USERNAME": os.environ['MAIL_USERNAME'],
    "MAIL_PASSWORD": os.environ['MAIL_PASSWORD']
}
app.config.update(mail_settings)
mail = Mail(app)

##### 2. Variables #####
# Latest library version
latest_library_version = os.environ['LIBRARY_VERSION']

#######################################################
#######################################################
########## 2. Server
#######################################################
#######################################################

#############################################
########## 1. Home
#############################################

@app.route(entry_point, methods=['GET', 'POST'])
def index():
	return render_template('index.html')

#############################################
########## 2. Generate API
#############################################

@app.route(entry_point+'/api/generate', methods=['GET', 'POST'])
def generate():

	# Get tool metadata
	tool_metadata = pd.read_sql_table('tool', engine).set_index('tool_string').to_dict(orient='index')
	core_script_metadata = pd.read_sql_table('core_scripts', engine).set_index('option_string').to_dict(orient='index')
	parameter_dataframe = pd.read_sql_query('SELECT tool_string, parameter_string, value, `default` FROM tool t LEFT JOIN parameter p ON t.id=p.tool_fk LEFT JOIN parameter_value pv ON p.id=pv.parameter_fk', engine).set_index(['tool_string', 'parameter_string'])
	annotations = {'tools': tool_metadata, 'core_options': core_script_metadata, 'parameter_dataframe': parameter_dataframe}
	print('generating notebook...')
	
	# Try
	try:
		# GET request
		if request.method == 'GET':

			# Open example.json
			with open('example.json', 'r') as openfile:
				notebook_configuration = json.loads(openfile.read())

			# Generate, Execute and Convert to HTML
			notebook = NG.generate_notebook(notebook_configuration, annotations, library_version=False)
			notebook = NM.execute_notebook(notebook, execute=False, to_html=True, kernel_name='python3')

			# Return
			return notebook

		### Production
		else:
			# Get Configuration
			notebook_configuration = request.json
			# notebook_configuration = {"notebook": {"title": "GSE88741 Analysis Notebook | BioJupies", "live": "False", "version": "v0.9.3"}, "tools": [{"tool_string": "pca", "parameters": {"nr_genes": "2500", "normalization": "logCPM", "z_score": "True", "plot_type": "interactive"}}, {"tool_string": "clustergrammer", "parameters": {"nr_genes": "2500", "normalization": "logCPM", "z_score": "True"}}, {"tool_string": "library_size_analysis", "parameters": {"plot_type": "interactive"}}], "data": {"source": "archs4", "parameters": {"gse": "GSE88741", "platform": "GPL16791"}}, "signature": {}, "terms": []}

			# Check if notebook exists
			session = Session()
			matching_notebook = session.query(tables['notebook'].columns['notebook_uid']).filter(tables['notebook'].columns['notebook_configuration'] == json.dumps(notebook_configuration)).all()
			

			# Return existing notebook
			if len(matching_notebook):

				# Get URL
				notebook_uid = matching_notebook[0].notebook_uid

			# Generate new notebook
			else:

				# Generate notebook
				notebook = NG.generate_notebook(notebook_configuration, annotations)

				# Execute notebook
				notebook, time = NM.execute_notebook(notebook)

				# Get URL
				notebook_uid = NM.upload_notebook(notebook, notebook_configuration, time, engine)

			# Return
			return json.dumps({'notebook_uid': notebook_uid, 'notebook_url': 'http://amp.pharm.mssm.edu/biojupies/notebook/'+notebook_uid})

	except Exception as e:

		# Raise
		if request.method == 'GET':
			raise
		else:

			# Get Configuration
			notebook_configuration = request.json

			# Get error message
			ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')

			# Get response
			error_response = NM.log_error(notebook_configuration, ansi_escape.sub('', str(e)), annotations, engine, app, mail)

			return error_response

#############################################
########## 3. Download
#############################################

@app.route(entry_point+'/download', methods=['GET', 'POST'])
def download():

	# Load HDF5 File
	h5 = '/download/{gse}-{platform}.h5'.format(**request.args.to_dict())
	with open(h5, 'wb') as openfile:
		openfile.write(urllib.request.urlopen('https://storage.googleapis.com/archs4-packages-{}/'.format(request.args.get('version'))+h5.split('/')[-1]).read())
	f = h5py.File(h5, 'r')
		
	# Get data
	if request.args.get('content') == 'expression':
		results = pd.DataFrame(data=f['data']['expression'].value, columns=[x for x in f['meta']['gene']['symbol'].value], index=[x for x in f['meta']['sample']['Sample_geo_accession'].value]).T
		results.index.name = 'gene_symbol'
		outfile = request.args.to_dict()['gse']+'-expression.txt'
	elif request.args.get('content') == 'metadata':
		results = pd.DataFrame({key: [x for x in value.value] if type(value) == h5py._hl.dataset.Dataset else [x for x in [y for y in value.items()][0][1].value] for key, value in f['meta']['sample'].items()}).set_index('Sample_geo_accession').rename(columns={'Sample_title': 'Sample Title'})
		outfile = request.args.to_dict()['gse']+'-metadata.txt'
		
	# Convert to string
	results_str = results.to_csv(sep='\t')

	return Response(results_str, mimetype="txt", headers={"Content-disposition": "attachment; filename={}.txt".format(outfile)})

#######################################################
#######################################################
########## 3. Extension API
#######################################################
#######################################################

#############################################
########## 1. Version API
#############################################

@app.route(entry_point+'/api/version', methods=['GET', 'POST'])
def version():
	return json.dumps({'latest_library_version': latest_library_version})

#############################################
########## 2. Samples API
#############################################

@app.route(entry_point+'/api/samples', methods=['GET', 'POST'])
def samples():

	# Get GSE
	gse_list = json.loads(request.get_data()).get('gse')
	if gse_list:
		
		# Get Sample Dataframe
		session = Session()
		db_query = session.query(
				tables['dataset_v6'].columns['dataset_accession'].label('gse'),
				tables['sample_v6'].columns['sample_title'],
				tables['sample_v6'].columns['sample_accession'].label('gsm'),
				tables['platform_v6'].columns['platform_accession'].label('gpl')) \
			.join(tables['sample_v6']) \
			.join(tables['platform_v6']) \
			.filter(tables['dataset_v6'].columns['dataset_accession'].in_(gse_list)).all()
		session.close()
		sample_dataframe = pd.DataFrame(db_query).set_index('gse')

		# Initialize result dict
		result = {gse:{} for gse in gse_list}

		# Loop through series
		for gse in sample_dataframe.index.unique():

			# Check if series has over 3 samples
			if len(sample_dataframe.loc[gse].index) > 3:
				platforms = sample_dataframe.loc[gse]['gpl'].unique()

				# Add platforms
				for platform in platforms:
					if len(sample_dataframe.loc[gse].set_index('gpl').loc[platform].index) > 3:
						result[gse][platform] = sample_dataframe.loc[gse].set_index('gpl').loc[platform].sort_values('sample_title').to_dict(orient='records')

		# Return
		return json.dumps(result)

#############################################
########## 3. Tools API
#############################################

@app.route(entry_point+'/api/tools', methods=['GET', 'POST'])
def tools():

	# Get data
	tool_dict = pd.read_sql_query('SELECT id, tool_string, tool_name, tool_description, default_selected, requires_signature FROM tool', engine, index_col='id').to_dict(orient='index')
	parameter_dataframe = pd.read_sql_query('SELECT * FROM parameter', engine, index_col='tool_fk')
	parameter_value_dataframe = pd.read_sql_query('SELECT * FROM parameter_value', engine, index_col='parameter_fk').drop('id', axis=1)

	# Add parameter values
	parameter_value_dict = {x:pd.DataFrame(parameter_value_dataframe).loc[x].to_dict(orient='records') for x in parameter_value_dataframe.index.unique()}
	parameter_dataframe['values'] = [parameter_value_dict.get(x, []) for x in parameter_dataframe['id']]
	parameter_dataframe.drop('id', axis=1, inplace=True)

	# Add parameters
	parameter_dict = {x: (parameter_dataframe.loc[x] if isinstance(parameter_dataframe.loc[x], pd.DataFrame) else parameter_dataframe.loc[x].to_frame().T).to_dict(orient='records') for x in parameter_dataframe.index.unique()}
	for tool_id, tool_data in tool_dict.items():
		tool_data['parameters'] = parameter_dict.get(tool_id, [])

	# Reindex
	tool_dict = pd.DataFrame(tool_dict).T.set_index('tool_string', drop=False).to_dict(orient='index')

	# Get sections
	statement = 'SELECT s.id, section_name, tool_string FROM section s LEFT JOIN tool t ON s.id=t.section_fk'
	if not dev:
		statement += ' WHERE t.display = 1'
	section_dict = pd.read_sql_query(statement, engine).groupby(['id', 'section_name']).aggregate(lambda x: tuple(x)).reset_index().drop('id', axis=1).to_dict(orient='records')

	return json.dumps({'tools': tool_dict, 'sections': section_dict})


#######################################################
#######################################################
########## 4. Error Handlers
#######################################################
#######################################################

#############################################
########## 1. 404
#############################################
@app.errorhandler(404)
def page_not_found(e):
    return redirect(url_for('generate'))

#######################################################
#######################################################
########## Run App
#######################################################
#######################################################

#############################################
########## 1. Run
#############################################
if __name__ == "__main__":
	app.run(debug=True, host='0.0.0.0')
