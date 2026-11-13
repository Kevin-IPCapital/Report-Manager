import { render } from 'preact';
import { App } from './App';
import './styles.css';
import './grid.css';   // after styles.css, so a column class wins over a rule for the same element

render(<App />, document.getElementById('app'));
