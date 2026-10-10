pipeline {
    agent any

    // Check GitHub for new commits every 2 minutes
    triggers {
        pollSCM('H/2 * * * *')
    }

    environment {
        IMAGE = 'aceest-app'
    }

    stages {
        stage('Checkout') {
            steps {
                echo 'Getting the latest code from GitHub'
                deleteDir()    // start from an empty workspace for a clean build
                checkout scm
            }
        }

        stage('Build') {
            steps {
                echo 'Creating a fresh virtual environment and installing dependencies'
                sh '''
                    python3 -m venv venv
                    venv/bin/pip install -r requirements.txt
                '''
            }
        }

        stage('Test') {
            steps {
                echo 'Running the Pytest suite'
                sh 'venv/bin/python -m pytest -v --junitxml=test-results.xml'
            }
            post {
                always {
                    junit 'test-results.xml'
                }
            }
        }

        stage('SonarQube Quality Gate') {
            steps {
                echo 'Running SonarQube analysis - the build stops if the quality gate fails'
                withCredentials([string(credentialsId: 'sonar-token', variable: 'SONAR_TOKEN')]) {
                    sh '''
                        /opt/sonar-scanner/bin/sonar-scanner \
                          -Dsonar.host.url=http://localhost:9000 \
                          -Dsonar.token=$SONAR_TOKEN \
                          -Dsonar.qualitygate.wait=true
                    '''
                }
            }
        }

        stage('Docker Build') {
            steps {
                echo 'Building the Docker image'
                sh 'docker build -t $IMAGE:$BUILD_NUMBER -t $IMAGE:latest .'
            }
        }

        stage('Test in Container') {
            steps {
                echo 'Running the tests again inside the image'
                sh 'docker run --rm $IMAGE:$BUILD_NUMBER python -m pytest -q'
            }
        }

        stage('Deploy to Staging') {
            steps {
                echo 'Deploying to staging (port 5001)'
                sh 'docker rm -f aceest-staging || true'
                sh 'docker run -d --name aceest-staging -p 5001:5000 $IMAGE:$BUILD_NUMBER'
                retry(10) {
                    sleep 3
                    sh 'curl -f http://localhost:5001/health'
                }
            }
        }

        stage('Deploy to Production') {
            steps {
                echo 'Staging is healthy - deploying to production (port 5000)'
                sh 'docker rm -f aceest-production || true'
                sh 'docker run -d --name aceest-production -p 5000:5000 $IMAGE:$BUILD_NUMBER'
                retry(10) {
                    sleep 3
                    sh 'curl -f http://localhost:5000/health'
                }
            }
        }
    }

    post {
        success {
            echo 'Done - staging is on port 5001 and production on port 5000'
        }
        failure {
            echo 'Pipeline failed - nothing after the failed stage was deployed'
        }
    }
}
